"""Script de génération de données de test pour MongoDB.

Crée 10 000 clients fictifs dans la collection 'customers' de la base 'demo_ecommerce'
en utilisant Faker avec la locale française.
"""

import os

from faker import Faker
from pymongo import MongoClient

fake = Faker('fr_FR')

mongo_host = os.getenv("MONGO_HOST", "mcp_mongo")
mongo_port = os.getenv("MONGO_PORT", "27017")
mongo_user = os.getenv("MONGODB_USER", "")
mongo_password = os.getenv("MONGODB_PASSWORD", "")

client = MongoClient(f"mongodb://{mongo_user}:{mongo_password}@{mongo_host}:{mongo_port}/")
db = client["demo_ecommerce"]

customers = db.customers
customers.drop()

batch = []
for i in range(10000):
    batch.append({
        "firstname": fake.first_name(),
        "lastname": fake.last_name(),
        "email": fake.email(),
        "phone": fake.phone_number(),
        "city": fake.city(),
        "country": fake.country(),
        "created_at": fake.date_time_this_year()
    })
    if len(batch) >= 1000:
        customers.insert_many(batch)
        batch = []

if batch:
    customers.insert_many(batch)

print(f"  {customers.count_documents({})} documents insérés dans demo_ecommerce.customers")
