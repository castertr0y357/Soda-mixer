import os
import django
import sys
import uuid
import requests

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'soda_mixer')))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'soda_mixer.settings')
django.setup()

from flavors.models import SystemConfiguration

conf = SystemConfiguration.objects.first()
h = {'Authorization': 'Bearer ' + conf.mealie_api_key}
u = conf.mealie_url.rstrip('/')

# 1. Initialize Recipe
r = requests.post(u + '/api/recipes', json={'name': 'API Diagnostics Script'}, headers=h, allow_redirects=True).json()
slug = r.get('slug') or r.get('id') or r
print('Created:', slug)

patch_url = u + '/api/recipes/' + slug

# 2. Test Base Fields
print('Base Patch:', requests.patch(patch_url, json={'recipeYield': '1 serving', 'description': 'desc'}, headers=h).text)

# 3. Test Instructions
instructions = [
    {"id": str(uuid.uuid4()), "title": "Step 1", "text": "Do a thing"}
]
print('Instructions Patch:', requests.patch(patch_url, json={'recipeInstructions': instructions}, headers=h).text)

# 4. Test Ingredients (Complex)
ingredients = [
    {
        "referenceId": str(uuid.uuid4()),
        "quantity": 1.5,
        "unit": {"name": "oz"},
        "food": {"name": "Syrup"},
        "note": "Test note",
        "display": "1.5 oz Syrup (Test note)",
        "isFood": True,
        "disableAmount": False
    }
]
print('Ingredients Patch Complex:', requests.patch(patch_url, json={'recipeIngredient': ingredients}, headers=h).text)

# 5. Test Ingredients (Simple)
ingredients_simple = [
    {
        "referenceId": str(uuid.uuid4()),
        "quantity": 1.5,
        "note": "1.5 oz Syrup (Test note)",
        "display": "1.5 oz Syrup (Test note)",
    }
]
print('Ingredients Patch Simple:', requests.patch(patch_url, json={'recipeIngredient': ingredients_simple}, headers=h).text)
