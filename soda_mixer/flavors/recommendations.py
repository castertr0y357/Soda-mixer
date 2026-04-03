"""Recommendation engine for Soda Mixer."""

import random
from .models import Ingredient, Recipe, RecipeIngredient


# Compatibility matrix: which categories pair well together
CATEGORY_COMPATIBILITY = {
    'citrus': ['berry', 'tropical', 'herbal', 'sweet'],
    'berry': ['citrus', 'tropical', 'herbal', 'sweet'],
    'tropical': ['citrus', 'berry', 'spice', 'herbal'],
    'herbal': ['citrus', 'berry', 'tropical', 'sour'],
    'spice': ['citrus', 'tropical', 'berry', 'coffee'],
    'sweet': ['citrus', 'berry', 'sour', 'herbal', 'coffee'],
    'sour': ['sweet', 'herbal', 'citrus'],
    'artificial': ['citrus', 'berry', 'sweet', 'tropical'],
    'coffee': ['spice', 'sweet', 'herbal'],
}

# --- Name Generator ---

_INTENSITY_ADJECTIVES = {
    (1, 2): ['Gentle', 'Soft', 'Mellow', 'Subtle', 'Light', 'Easy'],
    (3, 3): ['Balanced', 'Classic', 'Smooth', 'Crisp', 'Fresh'],
    (4, 5): ['Bold', 'Vivid', 'Intense', 'Zesty', 'Punchy', 'Vibrant'],
}

_CATEGORY_NOUNS = {
    'citrus': ['Citrus Burst', 'Lemon Twist', 'Citrus Wave', 'Sunrise', 'Grove'],
    'berry': ['Berry Splash', 'Berry Bliss', 'Wild Berry', 'Forest Mix', 'Bramble'],
    'tropical': ['Tropical Dream', 'Island Breeze', 'Paradise', 'Tropicana', 'Lagoon'],
    'herbal': ['Garden Fizz', 'Herb Garden', 'Meadow Mist', 'Cool Breeze', 'Fresh Patch'],
    'spice': ['Spice Road', 'Autumn Spice', 'Warm Blend', 'Kick', 'Zest'],
    'sweet': ['Sweet Cloud', 'Sugar Rush', 'Sweet Harmony', 'Candy Pop', 'Velvet'],
    'sour': ['Sour Power', 'Tart Twist', 'Acid Rain', 'Sharp Edge', 'Tangy Drop'],
    'artificial': ['Fun Fusion', 'Cosmic Pop', 'Neon Fizz', 'Electric Mix', 'Galaxy Sip'],
    'coffee': ['Dark Roast', 'Morning Brew', 'Espresso Shot', 'Bean Blend', 'Roast'],
}

_FINISHERS = {
    'SODA': ['Fizz', 'Soda', 'Blend', 'Mix', 'Cooler', 'Splash', 'Delight', 'Special'],
    'COFFEE': ['Brew', 'Drip', 'Extraction', 'Press', 'Roast', 'Synergy', 'Laboratory'],
    'SLUSHIE': ['Chill', 'Glacier', 'Frost', 'Slush', 'Ice', 'Cryo', 'Zero'],
}


def generate_recipe_name(ingredient_ids, drink_type='SODA'):
    """
    Generate a creative, deterministic recipe name from a list of ingredient IDs.
    Returns a string name.
    """
    if not ingredient_ids:
        return "Mystery Mix"

    ingredients = list(Ingredient.objects.filter(id__in=ingredient_ids))
    if not ingredients:
        return "Mystery Mix"

    # Determine dominant category (most common)
    category_counts = {}
    for i in ingredients:
        category_counts[i.category] = category_counts.get(i.category, 0) + 1
    dominant_cat = max(category_counts, key=category_counts.get)

    # Average intensity
    avg_intensity = sum(i.intensity for i in ingredients) / len(ingredients)

    # Pick adjective based on intensity
    adjective = ''
    for (low, high), adjs in _INTENSITY_ADJECTIVES.items():
        if low <= avg_intensity <= high:
            adjective = adjs[len(ingredients) % len(adjs)]
            break

    # Pick noun from dominant category
    nouns = _CATEGORY_NOUNS.get(dominant_cat, ['Blend'])
    noun = nouns[sum(i.id for i in ingredients) % len(nouns)]

    # Optionally attach a finisher for variety
    use_finisher = (sum(i.id for i in ingredients) % 3) == 0
    finishers = _FINISHERS.get(drink_type, _FINISHERS['SODA'])
    finisher = finishers[len(ingredient_ids) % len(finishers)] if use_finisher else ''

    parts = [p for p in [adjective, noun, finisher] if p]
    return ' '.join(parts)


# --- Category Suggester ---

_PROFILE_CATEGORY_RULES = [
    # (rule_fn, suggested_category_name)
    (lambda stats: stats['sweetness'] > 3.5 and stats['acidity'] < 3, 'Sweet'),
    (lambda stats: stats['acidity'] > 3.5 and stats['sweetness'] < 3.5, 'Sour & Tangy'),
    (lambda stats: stats['acidity'] > 3 and stats['sweetness'] > 3, 'Refreshing'),
    (lambda stats: stats['bitterness'] > 3, 'Bold'),
    (lambda stats: stats['sweetness'] <= 2 and stats['acidity'] <= 2, 'Mellow'),
]

_INGREDIENT_CATEGORY_RULES = {
    'herbal': 'Refreshing',
    'tropical': 'Summer',
    'spice': 'Autumn',
    'citrus': 'Citrus Lover',
    'berry': 'Berry Life',
    'sweet': 'Sweet Tooth',
    'sour': 'Sour & Tangy',
    'coffee': 'Caffeine Lab',
}


def suggest_categories(ingredient_ids):
    """
    Return a list of suggested category name strings based on the ingredients chosen.
    """
    ingredients = list(Ingredient.objects.filter(id__in=ingredient_ids))
    if not ingredients:
        return []

    count = len(ingredients)
    stats = {
        'sweetness': sum(i.sweetness for i in ingredients) / count,
        'acidity': sum(i.acidity for i in ingredients) / count,
        'bitterness': sum(i.bitterness for i in ingredients) / count,
    }

    suggestions = set()

    # Profile-based rules
    for rule_fn, cat_name in _PROFILE_CATEGORY_RULES:
        try:
            if rule_fn(stats):
                suggestions.add(cat_name)
        except Exception:
            pass

    # Ingredient-category based rules
    for i in ingredients:
        if i.category in _INGREDIENT_CATEGORY_RULES:
            suggestions.add(_INGREDIENT_CATEGORY_RULES[i.category])

    return sorted(suggestions)


# pairing suggestions with intensity rules
def get_recommendation(ingredient_ids, drink_type='SODA'):
    """
    Get ingredient recommendations based on selected ingredients.
    """
    if not ingredient_ids:
        return {
            'recommended': _get_top_recommendations(drink_type),
            'recipes': [],
            'suggestions': []
        }
    
    selected_ingredients = Ingredient.objects.filter(id__in=ingredient_ids)
    if not selected_ingredients.exists():
        return get_recommendation([], drink_type)
    
    recommendations = []
    
    # Get recommended ingredients based on compatibility
    for ingredient in selected_ingredients:
        compatible_categories = CATEGORY_COMPATIBILITY.get(ingredient.category, [])
        matching_ingredients = Ingredient.objects.filter(
            category__in=compatible_categories
        ).exclude(id__in=ingredient_ids)
        
        # Filter by inventory
        matching_ingredients = matching_ingredients.filter(is_in_inventory=True)
        
        # Score by intensity balance
        for i in matching_ingredients:
            score = _calculate_compatibility_score(ingredient, i)
            # Espresso Synergy bonus
            if drink_type == 'COFFEE' and i.ingredient_type == 'COFFEE_BEAN':
                score += 5
                
            recommendations.append({
                'ingredient': i,
                'score': score,
                'reason': f"Pairs with {ingredient.name}"
            })
    
    # Sort and filter unique
    recommendations.sort(key=lambda x: x['score'], reverse=True)
    seen = set()
    top_recommendations = []
    for rec in recommendations:
        if rec['ingredient'].id not in seen:
            top_recommendations.append(rec)
            seen.add(rec['ingredient'].id)
        if len(top_recommendations) >= 5:
            break
    
    recipe_suggestions = _find_similar_recipes(selected_ingredients)
    
    return {
        'recommended': top_recommendations,
        'recipes': recipe_suggestions,
        'suggestions': list(selected_ingredients)
    }

def get_tiered_recommendation(base_id, secondary_id=None, drink_type='SODA'):
    """
    Get tiered recommendations (Secondary or Tertiary) based on selected base and optional secondary.
    """
    base_ingredient = Ingredient.objects.filter(id=base_id, is_in_inventory=True).first()
    if not base_ingredient:
        return {'recommended': []}

    recommendations = []
    
    if not secondary_id:
        # Looking for Secondary
        compat_cats = CATEGORY_COMPATIBILITY.get(base_ingredient.category, [])
        candidates = Ingredient.objects.filter(
            category__in=compat_cats, 
            is_in_inventory=True
        ).exclude(id=base_id)
        
        for cand in candidates:
            score = _calculate_compatibility_score(base_ingredient, cand)
            if drink_type == 'COFFEE' and cand.ingredient_type == 'COFFEE_BEAN':
                score += 5
            recommendations.append({
                'ingredient': cand,
                'score': score,
                'tier': 'secondary',
                'reason': f"Pairs nicely with {base_ingredient.name}"
            })
    else:
        # Looking for Tertiary
        sec_ingredient = Ingredient.objects.filter(id=secondary_id).first()
        if not sec_ingredient:
            return {'recommended': []}
            
        base_compat = set(CATEGORY_COMPATIBILITY.get(base_ingredient.category, []))
        sec_compat = set(CATEGORY_COMPATIBILITY.get(sec_ingredient.category, []))
        shared_compat = base_compat.intersection(sec_compat)
        
        candidates = Ingredient.objects.filter(
            category__in=shared_compat,
            is_in_inventory=True
        ).exclude(id__in=[base_id, secondary_id])
        
        for cand in candidates:
            score1 = _calculate_compatibility_score(base_ingredient, cand)
            score2 = _calculate_compatibility_score(sec_ingredient, cand)
            profile_score = _calculate_profile_balance(base_ingredient, sec_ingredient, cand)
            
            recommendations.append({
                'ingredient': cand,
                'score': score1 + score2 + profile_score,
                'tier': 'tertiary',
                'reason': f"Bridges {base_ingredient.name} and {sec_ingredient.name}"
            })
            
    recommendations.sort(key=lambda x: x['score'], reverse=True)
    return {'recommended': recommendations[:5]}


def _calculate_compatibility_score(i1, i2):
    """Calculate compatibility score between two ingredients."""
    score = 0
    if i1.category == i2.category:
        score -= 1
    intensity_diff = abs(i1.intensity - i2.intensity)
    score += intensity_diff * 2
    if i2.category in CATEGORY_COMPATIBILITY.get(i1.category, []):
        score += 3
    return score


def _calculate_profile_balance(i1, i2, cand):
    """Reward candidates that provide missing profile elements."""
    score = 0
    avg_sweet = (i1.sweetness + i2.sweetness) / 2.0
    avg_acid = (i1.acidity + i2.acidity) / 2.0
    
    if avg_sweet > 3.5 and (cand.acidity >= 3 or cand.bitterness >= 3):
        score += 3
    if avg_acid > 3.5 and cand.sweetness >= 3:
        score += 3
        
    return score


def calculate_recipe_stats(recipe_ingredients):
    """
    Calculate weighted stats for a given mix of RecipeIngredients.
    """
    total_vol = sum(ri.amount for ri in recipe_ingredients)
    if total_vol == 0:
        return {'sweetness': 0, 'acidity': 0, 'bitterness': 0}
        
    sweet = sum(ri.ingredient.sweetness * ri.amount for ri in recipe_ingredients) / total_vol
    acid = sum(ri.ingredient.acidity * ri.amount for ri in recipe_ingredients) / total_vol
    bitter = sum(ri.ingredient.bitterness * ri.amount for ri in recipe_ingredients) / total_vol
    
    return {
        'sweetness': round(sweet, 1),
        'acidity': round(acid, 1),
        'bitterness': round(bitter, 1)
    }


def _get_top_recommendations(drink_type='SODA'):
    """Get top recommended ingredients."""
    recommendations = []
    
    # Filter by inventory and type if possible
    query = Ingredient.objects.filter(is_in_inventory=True)
    if drink_type == 'COFFEE':
        query = query.filter(ingredient_type='COFFEE_BEAN')
        
    popular = query[:10]
    
    for ingredient in popular:
        compat_cats = CATEGORY_COMPATIBILITY.get(ingredient.category, [])
        matching = Ingredient.objects.filter(category__in=compat_cats, is_in_inventory=True).exclude(id=ingredient.id)[:3]
        
        for match in matching:
            recommendations.append({
                'ingredient': match,
                'score': 5,
                'reason': f"Popular pairing with {ingredient.name}"
            })
    
    return recommendations[:10]


def _find_similar_recipes(selected_ingredients):
    """Find recipes that use the selected ingredients."""
    ingredient_ids = [i.id for i in selected_ingredients]
    matching_recipe_ingredients = RecipeIngredient.objects.filter(ingredient_id__in=ingredient_ids)
    
    recipe_scores = {}
    for ri in matching_recipe_ingredients:
        recipe_id = ri.recipe.id
        recipe_scores[recipe_id] = recipe_scores.get(recipe_id, 0) + 1
    
    sorted_recipes = sorted(recipe_scores.items(), key=lambda x: x[1], reverse=True)
    
    similar_recipes = []
    for recipe_id, matches in sorted_recipes[:5]:
        recipe = Recipe.objects.get(id=recipe_id)
        all_ingredients = recipe.recipe_ingredients.all()
        
        ingredients_data = [{
            'id': ri.ingredient.id,
            'name': ri.ingredient.name,
            'amount': ri.amount,
            'intensity': ri.ingredient.intensity,
            'category': ri.ingredient.category
        } for ri in all_ingredients]
        
        similar_recipes.append({
            'id': recipe.id,
            'name': recipe.name,
            'drink_type': recipe.get_drink_type_display(),
            'description': recipe.description,
            'ingredients': ingredients_data,
            'match_count': matches,
            'updated_at': recipe.updated_at
        })
    
    return similar_recipes