import os
import django
from django.test import Client

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "plfog.settings")
django.setup()

c = Client()
response = c.get("/classes/blacksmithing-101-glen-6226-6926-61626/")
print(f"Status Code: {response.status_code}")
if response.status_code >= 400:
    print(response.content.decode("utf-8")[:1000])
