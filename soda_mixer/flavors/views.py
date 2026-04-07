"""Views for Soda Mixer flavors and recipes."""

from django.shortcuts import render, get_object_or_404, redirect
from django.db.models import Count, Avg
from django.utils import timezone
from datetime import timedelta
from django.core import serializers
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import user_passes_test
import json
import requests
import traceback
from django.contrib.auth import login, logout, authenticate
from django.db import IntegrityError
from django.contrib import messages

from .models import Ingredient, Recipe, RecipeIngredient, MixHistory, MixHistoryIngredient, RecipeCategory, SystemConfiguration, LLMProvider
from .recommendations import (
    get_recommendation, get_tiered_recommendation, calculate_recipe_stats,
    generate_recipe_name, suggest_categories
)
from .ai_service import AIAssistant


def home(request):
    """Home page with ingredient mixer and Hall of Fame stats."""
    ingredients = Ingredient.objects.filter(is_in_inventory=True)
    # Optimized Archive Fetch
    recipes = Recipe.objects.prefetch_related('categories', 'recipe_ingredients__ingredient').order_by('-updated_at')[:50]

    # Hall of Fame stats by theme
    stats_by_theme = {}
    for theme in ['SODA', 'COFFEE', 'SLUSHIE']:
        try:
            mvp_ingredient = Ingredient.objects.filter(ingredient_usage__recipe__drink_type=theme).annotate(
                recipe_count=Count('ingredient_usage')
            ).order_by('-recipe_count').first()

            top_category = RecipeCategory.objects.filter(recipes__drink_type=theme).annotate(
                avg_rating=Avg('recipes__rating')
            ).filter(avg_rating__isnull=False).order_by('-avg_rating').first()

            signature_mix = Recipe.objects.filter(drink_type=theme).annotate(
                history_count=Count('history_entry')
            ).order_by('-history_count').first()

            stats_by_theme[theme] = {
                'mvp_ingredient': mvp_ingredient.name if mvp_ingredient else '-',
                'top_category': top_category.name if top_category else '-',
                'signature_mix_name': signature_mix.name if signature_mix else '-',
                'signature_mix_url': f'/recipes/{signature_mix.id}/' if signature_mix else None,
            }
        except Exception as e:
            stats_by_theme[theme] = {
                'mvp_ingredient': '-',
                'top_category': '-',
                'signature_mix_name': '-',
                'signature_mix_url': None,
            }

    last_7_days = timezone.now() - timedelta(days=7)
    kitchen_velocity = MixHistory.objects.filter(mixed_at__gte=last_7_days).count()

    return render(request, 'flavors/home.html', {
        'ingredients': ingredients,
        'recipes': recipes,
        'velocity': kitchen_velocity,
        'stats_json': json.dumps(stats_by_theme),
    })


def ingredient_list(request):
    """List all available ingredients."""
    category = request.GET.get('category')
    ingredients = Ingredient.objects.all().order_by('category', 'name')
    
    if category:
        ingredients = ingredients.filter(category=category)
        
    used_categories = Ingredient.objects.values_list('category', flat=True).distinct().order_by('category')
    # Deduplicate after normalization (handles existing mixed-case DB entries)
    seen = {}
    for c in used_categories:
        key = c.strip().lower()
        if key not in seen:
            seen[key] = c.strip().title()
    categories = sorted(seen.items(), key=lambda x: x[0])

    # Include fallback defaults if DB is totally empty
    if not categories:
        categories = [
            ('citrus', 'Citrus'), ('berry', 'Berry'), ('tropical', 'Tropical'),
            ('coffee', 'Coffee Profile')
        ]

    return render(request, 'flavors/ingredient_list.html', {
        'ingredients': ingredients,
        'categories': categories,
        'all_categories': RecipeCategory.objects.all().order_by('name')
    })


@require_http_methods(["POST"])
def add_ingredient(request):
    """Add a new ingredient from the frontend modal."""
    # We allow any logged-in user or just staff? The user requested staff check for *delete*. 
    # For *add*, standard user/local should be fine, or we can just allow it.
    name = request.POST.get('name', '').strip()
    ingredient_type = request.POST.get('ingredient_type', 'SODA_SYRUP')
    category = request.POST.get('category', 'citrus').strip().lower()
    description = request.POST.get('description', '')
    
    intensity = request.POST.get('intensity', 3)
    sweetness = request.POST.get('sweetness', 3)
    acidity = request.POST.get('acidity', 3)
    bitterness = request.POST.get('bitterness', 1)
    
    systems = request.POST.getlist('compatible_systems')
    compatible_systems = ",".join(systems) if systems else "SODA,COFFEE,SLUSHIE"

    if name:
        try:
            Ingredient.objects.create(
                name=name,
                ingredient_type=ingredient_type,
                category=category,
                description=description,
                intensity=intensity,
                sweetness=sweetness,
                acidity=acidity,
                bitterness=bitterness,
                compatible_systems=compatible_systems,
                is_in_inventory=True
            )
        except IntegrityError:
            messages.error(request, f"Registry Conflict: The reagent '{name}' is already indexed in the Laboratory repository.")
    return redirect('ingredient_list')


@require_http_methods(["POST"])
def edit_ingredient(request, pk):
    """Modify an existing ingredient."""
    if not request.user.is_staff:
        # Simplistic authorization fallback matching delete mechanism
        return redirect('ingredient_list')
        
    ingredient = get_object_or_404(Ingredient, pk=pk)
    ingredient.name = request.POST.get('name', ingredient.name).strip()
    ingredient.ingredient_type = request.POST.get('ingredient_type', ingredient.ingredient_type)
    
    category = request.POST.get('category', '').strip().lower()
    if category:
        ingredient.category = category
        
    ingredient.description = request.POST.get('description', ingredient.description)
    
    systems = request.POST.getlist('compatible_systems')
    if systems:
        ingredient.compatible_systems = ",".join(systems)
    
    try:
        ingredient.intensity = int(request.POST.get('intensity', ingredient.intensity))
        ingredient.sweetness = int(request.POST.get('sweetness', ingredient.sweetness))
        ingredient.acidity = int(request.POST.get('acidity', ingredient.acidity))
        ingredient.bitterness = int(request.POST.get('bitterness', ingredient.bitterness))
    except ValueError:
        pass
        
    try:
        ingredient.save()
    except IntegrityError:
        messages.error(request, f"Registry Conflict: The name '{ingredient.name}' is already assigned to another reagent.")
        return redirect('ingredient_list')
        
    return redirect('ingredient_list')


@user_passes_test(lambda u: u.is_staff)
@require_http_methods(["POST"])
def delete_ingredient(request, pk):
    """Delete an ingredient, restricted to staff."""
    ingredient = get_object_or_404(Ingredient, pk=pk)
    ingredient.delete()
    return redirect('ingredient_list')


@user_passes_test(lambda u: u.is_staff)
@require_http_methods(["POST"])
def delete_category(request, pk):
    """Delete a recipe category, restricted to staff."""
    category = get_object_or_404(RecipeCategory, pk=pk)
    category.delete()
    return redirect('ingredient_list')


@csrf_exempt
@require_http_methods(["POST"])
def create_category_api(request):
    """Create a new RecipeCategory via AJAX."""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    try:
        data = json.loads(request.body)
        name = data.get('name', '').strip()
        color = data.get('color', 'bg-secondary')
        if not name:
            return JsonResponse({'error': 'Name is required.'}, status=400)
        cat, created = RecipeCategory.objects.get_or_create(name=name, defaults={'color': color})
        return JsonResponse({'status': 'success', 'id': cat.id, 'name': cat.name, 'color': cat.color, 'created': created})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def delete_recipe_category_api(request, pk):
    """Delete a RecipeCategory via AJAX."""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    try:
        cat = get_object_or_404(RecipeCategory, pk=pk)
        cat.delete()
        return JsonResponse({'status': 'success'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def delete_ingredient_profile_api(request):
    """Delete an ingredient base profile (category slug) via AJAX.
    Reassigns all ingredients with that profile to 'other'."""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    try:
        data = json.loads(request.body)
        profile = data.get('profile', '').strip().lower()
        if not profile:
            return JsonResponse({'error': 'Profile name required.'}, status=400)
        count = Ingredient.objects.filter(category=profile).update(category='other')
        return JsonResponse({'status': 'success', 'reassigned': count})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


def ingredient_detail(request, pk):
    """Show a single ingredient's details."""
    ingredient = get_object_or_404(Ingredient, pk=pk)

    compatible_cats = _get_compatible_categories(ingredient.category)
    compatible_ingredients = Ingredient.objects.filter(
        category__in=compatible_cats
    ).exclude(pk=ingredient.pk)[:5]

    return render(request, 'flavors/ingredient_detail.html', {
        'ingredient': ingredient,
        'compatible': compatible_ingredients
    })



def recipe_list(request):
    """List all saved recipes, optionally filtered by category."""
    category_id = request.GET.get('category')
    all_categories = RecipeCategory.objects.all().order_by('name')

    # Optimized Archive Fetch with prefetching
    recipes = Recipe.objects.prefetch_related('categories', 'recipe_ingredients__ingredient').all().order_by('-updated_at')
    if category_id:
        recipes = recipes.filter(categories__id=category_id)

    return render(request, 'flavors/recipe_list.html', {
        'recipes': recipes,
        'all_categories': all_categories,
        'active_category_id': int(category_id) if category_id else None,
    })


def recipe_detail(request, pk):
    """Show a single recipe's details."""
    recipe = get_object_or_404(Recipe, pk=pk)
    stats = calculate_recipe_stats(recipe.recipe_ingredients.all())
    all_categories = RecipeCategory.objects.all().order_by('name')

    return render(request, 'flavors/recipe_detail.html', {
        'recipe': recipe,
        'stats': stats,
        'all_categories': all_categories,
    })


def create_recipe(request):
    """Create a new recipe."""
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        category_ids = request.POST.getlist('categories')
        drink_type = request.POST.get('drink_type', 'SODA')

        ingredient_ids = []
        for key, value in request.POST.items():
            if key.startswith('amount_'):
                ingredient_id = key.replace('amount_', '')
                ingredient_ids.append(ingredient_id)
            elif key == 'ingredients':
                ingredient_ids.extend(request.POST.getlist('ingredients'))
            elif key.startswith('ingredient_'):
                ingredient_id = key.replace('ingredient_', '')
                ingredient_ids.append(ingredient_id)

        if not name:
            return render(request, 'flavors/create_recipe.html', {
                'error': 'Recipe name is required',
                'all_categories': RecipeCategory.objects.all(),
            })

        recipe = Recipe.objects.create(
            name=name, 
            description=description,
            drink_type=drink_type,
            brew_method=request.POST.get('brew_method'),
            grind_size=request.POST.get('grind_size'),
            water_temp_c=request.POST.get('water_temp_c') or None,
            brew_time_sec=request.POST.get('brew_time_sec') or None,
            total_water_g=request.POST.get('total_water_g') or None,
        )

        if category_ids:
            recipe.categories.set(RecipeCategory.objects.filter(id__in=category_ids))

        for ingredient_id in set(ingredient_ids):
            try:
                amount = float(request.POST.get(f'amount_{ingredient_id}', 1.0))
                notes = request.POST.get(f'notes_{ingredient_id}', '')
                RecipeIngredient.objects.create(
                    recipe=recipe,
                    ingredient_id=ingredient_id,
                    amount=amount,
                    notes=notes
                )
            except (ValueError, ValidationError):
                pass

        return redirect('recipe_detail', pk=recipe.pk)

    ingredients = Ingredient.objects.all()
    return render(request, 'flavors/create_recipe.html', {
        'ingredients': ingredients,
        'all_categories': RecipeCategory.objects.all(),
    })


def edit_recipe(request, pk):
    """Edit an existing recipe."""
    recipe = get_object_or_404(Recipe, pk=pk)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        category_ids = request.POST.getlist('categories')

        recipe.name = name
        recipe.description = description
        recipe.drink_type = request.POST.get('drink_type', recipe.drink_type)
        recipe.brew_method = request.POST.get('brew_method', recipe.brew_method)
        recipe.grind_size = request.POST.get('grind_size', recipe.grind_size)
        
        # Numeric coffee fields
        for field in ['water_temp_c', 'brew_time_sec', 'total_water_g']:
            val = request.POST.get(field)
            if val is not None and val.strip() != '':
                try:
                    setattr(recipe, field, float(val))
                except (ValueError, TypeError):
                    setattr(recipe, field, None)
            else:
                setattr(recipe, field, None)
        
        recipe.save()
        recipe.categories.set(RecipeCategory.objects.filter(id__in=category_ids))

        # Handle ingredients - simplified approach for now: clear and re-add 
        # based on 'amount_ID' keys which we'll send from the template.
        recipe.recipe_ingredients.all().delete()
        
        for key, value in request.POST.items():
            if key.startswith('amount_'):
                try:
                    ingredient_id = int(key.replace('amount_', ''))
                    amount = float(value)
                    notes = request.POST.get(f'notes_{ingredient_id}', '')
                    RecipeIngredient.objects.create(
                        recipe=recipe,
                        ingredient_id=ingredient_id,
                        amount=amount,
                        notes=notes
                    )
                except (ValueError, TypeError, Ingredient.DoesNotExist):
                    continue

        return redirect('recipe_detail', pk=recipe.pk)

    ingredients = Ingredient.objects.all()
    return render(request, 'flavors/edit_recipe.html', {
        'recipe': recipe,
        'ingredients': ingredients,
        'all_categories': RecipeCategory.objects.all(),
    })



def delete_recipe(request, pk):
    """Delete a recipe."""
    if request.method == 'POST':
        recipe = get_object_or_404(Recipe, pk=pk)
        recipe.delete()
        return redirect('recipe_list')

    recipe = get_object_or_404(Recipe, pk=pk)
    return render(request, 'flavors/delete_recipe.html', {'recipe': recipe})


def settings_view(request):
    """Settings page for backups and system management."""
    config = SystemConfiguration.get_config()
    providers = LLMProvider.objects.all().order_by('name')
    return render(request, 'flavors/settings.html', {
        'config': config,
        'providers': providers,
        'provider_types': LLMProvider.PROVIDER_CHOICES
    })


def export_data(request):
    """Export all laboratory data to a JSON dossier."""
    data = {
        'ingredients': serializers.serialize('json', Ingredient.objects.all()),
        'categories': serializers.serialize('json', RecipeCategory.objects.all()),
        'recipes': serializers.serialize('json', Recipe.objects.all()),
        'recipe_ingredients': serializers.serialize('json', RecipeIngredient.objects.all()),
        'mix_history': serializers.serialize('json', MixHistory.objects.all()),
        'mix_history_ingredients': serializers.serialize('json', MixHistoryIngredient.objects.all()),
    }
    
    response = HttpResponse(json.dumps(data), content_type='application/json')
    filename = f"beveragelab_dossier_{timezone.now().strftime('%Y%m%d_%H%M')}.json"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@csrf_exempt
@require_http_methods(["POST"])
def import_data(request):
    """Restore laboratory data from a JSON dossier (Merge by Name)."""
    if 'backup_file' not in request.FILES:
        return JsonResponse({'error': 'No file provided'}, status=400)

    try:
        raw_data = json.load(request.FILES['backup_file'])
        
        # 1. Restore Ingredients (Merge by Name)
        ingredient_map = {} # old_id -> new_object
        for i_data in serializers.deserialize('json', raw_data['ingredients']):
            i = i_data.object
            existing = Ingredient.objects.filter(name=i.name).first()
            if existing:
                ingredient_map[i_data.object.id] = existing
            else:
                i.id = None # Force new record
                i.save()
                ingredient_map[i_data.object.id] = i

        # 2. Restore Categories (Merge by Name)
        category_map = {}
        for c_data in serializers.deserialize('json', raw_data['categories']):
            c = c_data.object
            existing = RecipeCategory.objects.filter(name=c.name).first()
            if existing:
                category_map[c_data.object.id] = existing
            else:
                c.id = None
                c.save()
                category_map[c_data.object.id] = c

        # 3. Restore Recipes
        recipe_map = {}
        for r_data in serializers.deserialize('json', raw_data['recipes']):
            r = r_data.object
            old_id = r.id
            r.id = None
            r.save()
            recipe_map[old_id] = r

        # 4. Restore Recipe Ingredients
        for ri_data in serializers.deserialize('json', raw_data['recipe_ingredients']):
            ri = ri_data.object
            if ri.recipe_id in recipe_map and ri.ingredient_id in ingredient_map:
                ri.id = None
                ri.recipe = recipe_map[ri.recipe_id]
                ri.ingredient = ingredient_map[ri.ingredient_id]
                ri.save()

        # 5. Restore Mix History
        mix_map = {}
        for m_data in serializers.deserialize('json', raw_data['mix_history']):
            m = m_data.object
            old_id = m.id
            m.id = None
            if m.promoted_recipe_id and m.promoted_recipe_id in recipe_map:
                m.promoted_recipe = recipe_map[m.promoted_recipe_id]
            m.save()
            mix_map[old_id] = m

        # 6. Restore Mix History Ingredients
        for mhi_data in serializers.deserialize('json', raw_data['mix_history_ingredients']):
            mhi = mhi_data.object
            if mhi.mix_id in mix_map and mhi.ingredient_id in ingredient_map:
                mhi.id = None
                mhi.mix = mix_map[mhi.mix_id]
                mhi.ingredient = ingredient_map[mhi.ingredient_id]
                mhi.save()

        return JsonResponse({'status': 'success', 'message': 'Laboratory dossier integrated successfully'})

    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def save_settings_api(request):
    """Update global system configuration."""
    try:
        data = json.loads(request.body)
        config = SystemConfiguration.get_config()
        config.mealie_url = data.get('mealie_url', '').strip()
        config.mealie_api_key = data.get('mealie_api_key', '').strip()
        config.save()
        return JsonResponse({'status': 'success', 'message': 'Configuration saved successfully.'})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


def mix_history_list(request):
    """Show mix history with option to promote entries to recipes."""
    history = MixHistory.objects.prefetch_related('mix_ingredients__ingredient').order_by('-mixed_at')
    all_categories = RecipeCategory.objects.all().order_by('name')
    return render(request, 'flavors/mix_history.html', {
        'history': history,
        'all_categories': all_categories,
    })


# ── API endpoints ─────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def get_recommendations_api(request):
    """API endpoint for tiered ingredient recommendations."""
    try:
        data = json.loads(request.body)
        ingredient_ids = data.get('ingredient_ids', [])
        experimental = data.get('mode') == 'experimental' or data.get('experimental', False)

        if len(ingredient_ids) == 0:
            result = get_recommendation([], experimental=experimental)
            serialized_recs = [
                {
                    'id': r['ingredient'].id,
                    'name': r['ingredient'].name,
                    'category': r['ingredient'].category,
                    'intensity': r['ingredient'].intensity,
                    'score': r['score'],
                    'reason': r['reason'],
                    'tier': 'suggestions'
                } for r in result.get('recommended', [])
            ]
        elif len(ingredient_ids) == 1:
            result = get_tiered_recommendation(ingredient_ids[0], experimental=experimental)
            serialized_recs = [
                {
                    'id': r['ingredient'].id,
                    'name': r['ingredient'].name,
                    'category': r['ingredient'].category,
                    'intensity': r['ingredient'].intensity,
                    'score': r['score'],
                    'reason': r['reason'],
                    'tier': r.get('tier', 'secondary')
                } for r in result.get('recommended', [])
            ]
        else:
            result = get_tiered_recommendation(ingredient_ids[0], ingredient_ids[1], experimental=experimental)
            serialized_recs = [
                {
                    'id': r['ingredient'].id,
                    'name': r['ingredient'].name,
                    'category': r['ingredient'].category,
                    'intensity': r['ingredient'].intensity,
                    'score': r['score'],
                    'reason': r['reason'],
                    'tier': r.get('tier', 'tertiary')
                } for r in result.get('recommended', [])
            ]

        serialized = {'recommended': serialized_recs, 'recipes': []}
        return JsonResponse(serialized)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def add_recipe_api(request):
    """API endpoint for creating a recipe."""
    try:
        data = json.loads(request.body)

        name = data.get('name', '').strip()
        description = data.get('description', '')
        ingredients = data.get('ingredients', [])
        drink_type = data.get('drink_type', 'SODA')

        if not name:
            return JsonResponse({'error': 'Recipe name is required'}, status=400)

        recipe = Recipe.objects.create(
            name=name, 
            description=description,
            drink_type=drink_type,
            brew_method=data.get('brew_method'),
            grind_size=data.get('grind_size'),
            water_temp_c=data.get('water_temp_c'),
            brew_time_sec=data.get('brew_time_sec'),
            total_water_g=data.get('total_water_g'),
        )

        for ingredient_data in ingredients:
            ingredient_id = ingredient_data.get('ingredient_id')
            amount = ingredient_data.get('amount', 1.0)
            notes = ingredient_data.get('notes', '')

            if ingredient_id:
                RecipeIngredient.objects.create(
                    recipe=recipe,
                    ingredient_id=int(ingredient_id),
                    amount=float(amount),
                    notes=notes
                )

        return JsonResponse({
            'id': recipe.id,
            'name': recipe.name,
            'message': 'Recipe created successfully'
        }, status=201)

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def toggle_inventory_api(request, pk):
    ingredient = get_object_or_404(Ingredient, pk=pk)
    try:
        data = json.loads(request.body)
        ingredient.is_in_inventory = data.get('is_in_inventory', True)
        ingredient.save()
        return JsonResponse({'status': 'success', 'is_in_inventory': ingredient.is_in_inventory})
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def rate_recipe_api(request, pk):
    recipe = get_object_or_404(Recipe, pk=pk)
    try:
        data = json.loads(request.body)
        rating = data.get('rating', 0)
        if 0 <= rating <= 5:
            recipe.rating = rating
            recipe.save()
            return JsonResponse({'status': 'success', 'rating': recipe.rating})
        return JsonResponse({'error': 'Invalid rating value'}, status=400)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def save_mix_to_history_api(request):
    """Save an ad-hoc mix to history. Returns the history ID."""
    try:
        data = json.loads(request.body)
        ingredients = data.get('ingredients', [])  # [{id, amount}, ...]
        drink_type = data.get('drink_type', 'SODA')

        if not ingredients:
            return JsonResponse({'error': 'No ingredients provided'}, status=400)

        mix = MixHistory.objects.create(drink_type=drink_type)
        for item in ingredients:
            try:
                raw_id = item.get('id')
                if not raw_id:
                    continue
                
                ingredient_id = int(raw_id)
                amount = float(item.get('amount', 1.0))
                
                # Verify ingredient existence to prevent broken relations
                target_ing = Ingredient.objects.filter(id=ingredient_id).first()
                if target_ing:
                    MixHistoryIngredient.objects.create(
                        mix=mix,
                        ingredient=target_ing,
                        amount=amount
                    )
            except (ValueError, TypeError):
                continue

        return JsonResponse({'status': 'saved', 'mix_id': mix.id})
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def promote_mix_to_recipe_api(request, pk):
    """Promote a MixHistory entry to a saved Recipe."""
    mix = get_object_or_404(MixHistory, pk=pk)
    if mix.promoted_recipe:
        return JsonResponse({'error': 'Already promoted', 'recipe_id': mix.promoted_recipe.id}, status=400)

    try:
        data = json.loads(request.body)
        name = data.get('name', '').strip()
        description = data.get('description', '')
        category_ids = data.get('category_ids', [])

        if not name:
            return JsonResponse({'error': 'Recipe name is required'}, status=400)

        recipe = Recipe.objects.create(
            name=name, 
            description=description,
            drink_type=mix.drink_type
        )

        for mi in mix.mix_ingredients.all():
            if mi.ingredient:
                RecipeIngredient.objects.create(
                    recipe=recipe,
                    ingredient=mi.ingredient,
                    amount=mi.amount
                )
        
        if category_ids:
            try:
                recipe.categories.set(RecipeCategory.objects.filter(id__in=[int(cid) for cid in category_ids if str(cid).isdigit()]))
            except (ValueError, TypeError):
                pass

        mix.promoted_recipe = recipe
        mix.save()

        return JsonResponse({'status': 'promoted', 'recipe_id': recipe.id})
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def generate_name_api(request):
    """Return a suggested recipe name for a list of ingredient IDs."""
    try:
        data = json.loads(request.body)
        ingredient_ids = data.get('ingredient_ids', [])
        name = generate_recipe_name(ingredient_ids)
        return JsonResponse({'name': name})
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def get_category_suggestions_api(request):
    """Return suggested category names and all existing categories."""
    try:
        data = json.loads(request.body)
        ingredient_ids = data.get('ingredient_ids', [])
        suggested_names = suggest_categories(ingredient_ids)
        existing = list(RecipeCategory.objects.all().values('id', 'name', 'color'))
        return JsonResponse({'suggested': suggested_names, 'existing': existing})
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def create_category_api(request):
    """Create a new RecipeCategory."""
    try:
        data = json.loads(request.body)
        name = data.get('name', '').strip()
        color = data.get('color', 'bg-secondary')

        if not name:
            return JsonResponse({'error': 'Name required'}, status=400)

        cat, created = RecipeCategory.objects.get_or_create(
            name=name,
            defaults={'color': color}
        )
        return JsonResponse({
            'id': cat.id,
            'name': cat.name,
            'color': cat.color,
            'created': created
        }, status=201 if created else 200)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def update_recipe_categories_api(request, pk):
    """Set category list for a recipe."""
    recipe = get_object_or_404(Recipe, pk=pk)
    try:
        data = json.loads(request.body)
        category_ids = data.get('category_ids', [])
        recipe.categories.set(RecipeCategory.objects.filter(id__in=category_ids))
        return JsonResponse({'status': 'updated'})
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def export_recipe_to_mealie_api(request, pk):
    """Push a local recipe to a configured Mealie instance."""
    recipe = get_object_or_404(Recipe, pk=pk)
    config = SystemConfiguration.get_config()

    if not config.mealie_url or not config.mealie_api_key:
        return JsonResponse({'error': 'Mealie configuration is incomplete. Update settings in System Protocols.'}, status=400)

    url = config.mealie_url.rstrip('/') + '/api/recipes'
    headers = {
        'Authorization': f'Bearer {config.mealie_api_key}',
        'Content-Type': 'application/json'
    }

    # Construct description including Lab data
    lab_description = recipe.description or ""
    lab_description += "\n\n### Beverage Laboratory Telemetry\n"
    lab_description += f"- **Synthesis Type**: {recipe.get_drink_type_display()}\n"
    if recipe.brew_method:
        lab_description += f"- **Method**: {recipe.get_brew_method_display()}\n"
    
    # Format ingredients for Mealie
    mealie_ingredients = []
    for ring in recipe.recipe_ingredients.all():
        unit = "oz" if recipe.drink_type == "SLUSHIE" else ("g" if recipe.drink_type == "COFFEE" else "ml")
        mealie_ingredients.append({
            "note": f"{ring.amount} {unit} - {ring.ingredient.name} {f'({ring.notes})' if ring.notes else ''}".strip()
        })

    payload = {
        "name": recipe.name,
        "description": lab_description,
        "recipeYield": "1 serving",
        "recipeIngredient": mealie_ingredients,
        "tags": [cat.name for cat in recipe.categories.all()]
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        return JsonResponse({'status': 'success', 'message': f'Recipe successfully pushed to Mealie!'})
    except requests.exceptions.RequestException as e:
        return JsonResponse({'error': f'Failed to contact Mealie: {str(e)}'}, status=502)



def _get_compatible_categories(category):
    """Get list of categories that pair well with given category."""
    compatibility_map = {
        'citrus': ['berry', 'tropical', 'herbal', 'sweet'],
        'berry': ['citrus', 'tropical', 'herbal', 'sweet'],
        'tropical': ['citrus', 'berry', 'spice', 'herbal'],
        'herbal': ['citrus', 'berry', 'tropical', 'sour'],
        'spice': ['citrus', 'tropical', 'berry'],
        'sweet': ['citrus', 'berry', 'sour', 'herbal'],
        'sour': ['sweet', 'herbal', 'citrus'],
        'artificial': ['citrus', 'berry', 'sweet', 'tropical'],
        'coffee': ['spice', 'sweet', 'herbal'], # Added coffee compatibility
    }
    return compatibility_map.get(category, [])


@csrf_exempt
@require_http_methods(["POST"])
def ai_chat_api(request):
    """Bridge the user to the Creative Mixologist AI Assistant."""
    try:
        data = json.loads(request.body)
        user_message = data.get('message', '').strip()
        history = data.get('history', [])
        current_ingredients = data.get('current_ingredients', []) # List of names
        
        if not user_message and not current_ingredients:
            return JsonResponse({'error': 'No input provided'}, status=400)
            
        # Enrich prompt with laboratory context
        lab_context = ""
        if current_ingredients:
            lab_context = f"\n\n[Laboratory Context: Current Compound Contains: {', '.join(current_ingredients)}]"
        
        # Get full inventory registry for AI context
        all_ingredients = Ingredient.objects.filter(is_in_inventory=True)
        registry = []
        for ing in all_ingredients:
            registry.append(f"{ing.name} ({ing.get_ingredient_type_display()}, {ing.category.title() if ing.category else 'Misc'}, Intensity: {ing.intensity}/5)")
        inventory_context = "\n".join(registry)

        prompt = user_message + lab_context
        response_text = AIAssistant.chat(prompt, history=history, context=inventory_context)
        
        # Consistent response key for frontend
        return JsonResponse({'suggestion': response_text, 'status': 'success'})
    except Exception as e:
        print(f"DEBUG: Laboratory Communication Failure: {str(e)}")
        traceback.print_exc()
        return JsonResponse({'error': f"Laboratory Communication Failure: {str(e)}"}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def ai_keep_warm_api(request):
    """Check provider status and keep local models warm in VRAM."""
    status = AIAssistant.check_status()
    # 'pulsed' is the legacy key the frontend checks — map synchronized → pulsed
    return JsonResponse({'status': 'pulsed' if status == 'synchronized' else status})


@csrf_exempt
@require_http_methods(["POST"])
def save_llm_provider_api(request):
    """Manage multiple LLM providers (Cloud and Local)."""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Staff authentication required.'}, status=403)
        
    try:
        data = json.loads(request.body)
        pk = data.get('id')
        
        if pk:
            provider = get_object_or_404(LLMProvider, pk=pk)
        else:
            provider = LLMProvider()
            
        provider.name = data.get('name', 'New Provider').strip()
        provider.provider_type = data.get('provider_type', 'OPENAI')
        provider.api_key = data.get('api_key', '').strip()
        provider.base_url = data.get('base_url', '').strip()
        provider.default_model = data.get('default_model', '').strip()
        provider.is_enabled = data.get('is_enabled', False)
        provider.save()
        
        # If this is set as default
        if data.get('set_default', False):
            config = SystemConfiguration.get_config()
            config.default_llm_provider = provider
            config.save()
            
        # Trigger an immediate wakeup pulse if enabled
        if provider.is_enabled:
            AIAssistant.keep_warm()
            
        return JsonResponse({'status': 'success', 'id': provider.id})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def delete_llm_provider_api(request, pk):
    """Remove an LLM provider configuration."""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Forbidden'}, status=403)
    provider = get_object_or_404(LLMProvider, pk=pk)
    provider.delete()
    return JsonResponse({'status': 'success'})


@csrf_exempt
@require_http_methods(["POST"])
def fetch_provider_models_api(request, pk):
    """Fetch available models for a specific AI provider."""
    provider = get_object_or_404(LLMProvider, pk=pk)
    models = AIAssistant.list_models(provider)
    if models:
        return JsonResponse({'status': 'success', 'models': models})
    else:
        return JsonResponse({'status': 'error', 'message': 'Could not fetch models. Check API keys and base URL.'}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def discover_provider_models_api(request):
    """Fetch models for unsaved provider credentials."""
    if not request.user.is_staff:
        return JsonResponse({'error': 'Staff credentials required.'}, status=403)
        
    try:
        data = json.loads(request.body)
        provider_type = data.get('provider_type')
        api_key = data.get('api_key', '')
        base_url = data.get('base_url', '')
        
        if not provider_type:
            return JsonResponse({'error': 'Provider technology stack required.'}, status=400)
            
        # Create a temporary, unsaved object for the model list call
        temp_provider = LLMProvider(
            provider_type=provider_type,
            api_key=api_key,
            base_url=base_url
        )
        
        models = AIAssistant.list_models(temp_provider)
        if models:
            return JsonResponse({'status': 'success', 'models': models})
        else:
            return JsonResponse({'status': 'error', 'message': 'Discovery Protocol: No models found or credentials rejected.'}, status=400)
    except Exception as e:
        return JsonResponse({'error': f"Molecular Sync Failed: {str(e)}"}, status=500)


def login_view(request):
    """Render the dedicated laboratory access gate."""
    if request.user.is_authenticated:
        return redirect('home')
    return render(request, 'flavors/login.html')


@csrf_exempt
@require_http_methods(["POST"])
def login_api(request):
    """AJAX login endpoint for the laboratory."""
    try:
        data = json.loads(request.body)
        username = data.get('username')
        password = data.get('password')
        
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return JsonResponse({'status': 'success', 'user': user.username})
        else:
            return JsonResponse({'status': 'error', 'message': 'Invalid laboratory credentials.'}, status=401)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


@csrf_exempt
@require_http_methods(["POST"])
def logout_api(request):
    """AJAX logout endpoint."""
    logout(request)
    return JsonResponse({'status': 'success'})


@csrf_exempt
@require_http_methods(["POST"])
def ai_suggest_api(request):
    """Get proactive, structured multi-suggestions from the assistant."""
    try:
        data = json.loads(request.body)
        ingredients = data.get('ingredients', [])
        mode = data.get('mode', 'standard')
        exclude = data.get('exclude', []) # For more options
        
        if not ingredients:
            return JsonResponse({'error': 'No ingredients provided.'}, status=400)
            
        # Get full inventory registry for AI context
        all_ingredients = Ingredient.objects.filter(is_in_inventory=True)
        registry = [f"{ing.name} ({ing.get_ingredient_type_display()}, {ing.category.title() if ing.category else 'Misc'}, Intensity: {ing.intensity}/5)" for ing in all_ingredients]
        inventory_context = "\n".join(registry)

        # Implementation of 🧪 AI Protocol: Automated Retry & Verification
        # Up to 2 attempts to get structured data
        raw_suggestion = ""
        retry_note = None
        
        for attempt in range(2):
            raw_suggestion = AIAssistant.suggest_autonomous(
                ingredients, mode, 
                inventory=inventory_context, 
                exclude=exclude,
                retry_note=retry_note
            )
            
            # Use the new resilient JSON extractor
            suggested_data = AIAssistant._extract_json(raw_suggestion)
            
            if suggested_data and isinstance(suggested_data, list):
                # Molecular Resonance: Multi-tier fuzzy lookup
                enriched = []
                inventory_items = list(Ingredient.objects.filter(is_in_inventory=True))
                
                for item in suggested_data:
                    ing_name = item.get('name', '').strip().lower()
                    if not ing_name: continue
                    
                    target_obj = None
                    
                    # Tier 1: Exact Match
                    for inv in inventory_items:
                        if inv.name.lower() == ing_name:
                            target_obj = inv
                            break
                    
                    # Tier 2: Partial/Contains Match
                    if not target_obj:
                        for inv in inventory_items:
                            inv_lower = inv.name.lower()
                            if ing_name in inv_lower or inv_lower in ing_name:
                                target_obj = inv
                                break
                                
                    if target_obj:
                        # Calculate Molecular Resonance Level (based on intensity delta and a small random variance)
                        # We use the first ingredient as a baseline for intensity matching if available
                        import random
                        intensity_delta = 0
                        if ingredients:
                            # Robust Intensity matching: Case-insensitive lookup for the first protocol reagent
                            baseline_name = ingredients[0].strip().lower()
                            first_ing = next((inv for inv in inventory_items if inv.name.lower() == baseline_name), None)
                            
                            # Fallback to direct DB query if not in current registry slice
                            if not first_ing:
                                first_ing = Ingredient.objects.filter(name__iexact=ingredients[0]).first()
                            
                            base_intensity = first_ing.intensity if first_ing else 3
                            intensity_delta = abs(target_obj.intensity - base_intensity)
                        
                        # Resonance score: base 85% + up to 12% bonus for intensity proximity + small random flux
                        resonance = 85 + (max(0, 3 - intensity_delta) * 4) + random.uniform(0.1, 2.5)
                        
                        enriched.append({
                            'id': target_obj.id,
                            'name': target_obj.name,
                            'category': target_obj.category,
                            'intensity': target_obj.intensity,
                            'resonance': round(min(resonance, 99.8), 1),
                            'reason': item.get('reason', 'Molecular Affinity Match')
                        })
                
                if enriched:
                    # SUCCESS: Resonance established
                    return JsonResponse({'status': 'success', 'suggestions': enriched})
            
            # Incrementally improve prompt if we failed
            retry_note = "Your last synthesis signal was unparseable or used invalid nomenclature. Please adhere strictly to the JSON array format using the Inventory Registry's exact names."

        # Final Fallback: If all attempts failed to produce cards
        if not raw_suggestion or not raw_suggestion.strip():
            raw_suggestion = "System Failure: The Laboratory substrate returned an empty synthesis signal. Please try again."
            
        return JsonResponse({'status': 'success', 'suggestion': raw_suggestion})
    except Exception as e:
        traceback.print_exc()
        return JsonResponse({'error': f"Autonomous Suggestion Failed: {str(e)}"}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def ai_synthesize_api(request):
    """Generate a flavor synthesis report for a finalized compound."""
    try:
        data = json.loads(request.body)
        ingredients = data.get('ingredients', [])  # list of {name, intensity, category}
        drink_type = data.get('drink_type', 'SODA')
        
        if not ingredients:
            return JsonResponse({'error': 'No ingredients provided.'}, status=400)
        
        summary = AIAssistant.synthesize_flavor_summary(ingredients, drink_type)
        return JsonResponse({'status': 'success', 'summary': summary})
    except Exception as e:
        traceback.print_exc()
        return JsonResponse({'error': f"Synthesis Report Failed: {str(e)}"}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def ai_analyze_ingredient_api(request):
    """Use the LLM to synthesize a chemical flavor profile for a new ingredient."""
    try:
        data = json.loads(request.body)
        name = data.get('name')
        description = data.get('description', '')
        
        if not name:
            return JsonResponse({'error': 'Ingredient name required for analysis.'}, status=400)
            
        profile = AIAssistant.analyze_flavor_profile(name, description)
        if profile:
            return JsonResponse({'status': 'success', 'profile': profile})
        else:
            return JsonResponse({'error': 'Chemical analysis failed to yield structured data.'}, status=500)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
