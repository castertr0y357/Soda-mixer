import requests
import uuid
import sys
from soda_mixer.flavors.models import SystemConfiguration

conf = SystemConfiguration.objects.first()
h = {'Authorization': 'Bearer ' + conf.mealie_api_key}
u = conf.mealie_url.rstrip('/')

print("Initializing Shell...")
r_http = requests.post(u + '/api/recipes', json={'name': 'API Diagnostics Script'}, headers=h, allow_redirects=False)

if r_http.status_code in [301, 302, 307, 308]:
    u = r_http.headers['Location'].split('/api')[0]
    r_http = requests.post(u + '/api/recipes', json={'name': 'API Diagnostics Script'}, headers=h, allow_redirects=False)

r = r_http.json() if isinstance(r_http.json(), dict) else r_http.text
slug = r.get('slug') if isinstance(r, dict) else r.replace('"', '').strip()
patch_url = u + '/api/recipes/' + slug
print('Created:', slug)

print("Testing recipeIngredient with title...")
ingredients_title = [{
    "referenceId": str(uuid.uuid4()),
    "note": "Test note",
    "display": "1.5 oz Syrup",
    "title": "Syrup",
    "quantity": 1.5,
}]
print(requests.patch(patch_url, json={'recipeIngredient': ingredients_title}, headers=h).text)

print("Testing recipeIngredient with just text but title...")
ingredients_title_only = [{
    "referenceId": str(uuid.uuid4()),
    "title": "Syrup",
    "note": "1.5 oz Syrup"
}]
print(requests.patch(patch_url, json={'recipeIngredient': ingredients_title_only}, headers=h).text)
