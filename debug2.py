import requests
import uuid
from soda_mixer.flavors.models import SystemConfiguration

conf = SystemConfiguration.objects.first()
h = {'Authorization': 'Bearer ' + conf.mealie_api_key}
u = conf.mealie_url.rstrip('/')
r_http = requests.post(u + '/api/recipes', json={'name': 'API Diagnostics Script 2'}, headers=h, allow_redirects=False)
if r_http.status_code in [301, 302, 307, 308]:
    u = r_http.headers['Location'].split('/api')[0]
    r_http = requests.post(u + '/api/recipes', json={'name': 'API Diagnostics Script 2'}, headers=h, allow_redirects=False)
r = r_http.json() if isinstance(r_http.json(), dict) else r_http.text
slug = r.get('slug') if isinstance(r, dict) else r.replace('"', '').strip()
patch_url = u + '/api/recipes/' + slug

print("Testing instruction NO ID...")
inst = [{"text": "Just some text"}]
print(requests.patch(patch_url, json={'recipeInstructions': inst}, headers=h).text[:100])

print("Testing instruction WITH FULL schema...")
inst = [{"id": str(uuid.uuid4()), "title": "Preparation", "text": "Step 1", "ingredientReferences": []}]
print(requests.patch(patch_url, json={'recipeInstructions': inst}, headers=h).text[:100])
