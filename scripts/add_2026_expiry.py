import asyncio
import os
import sys
from datetime import date, datetime
import random
import string

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import AsyncSessionLocal
from app.db.models import Drug, DrugInventory
from sqlalchemy import select

async def add_2026_expiry_drugs():
    async with AsyncSessionLocal() as db:
        # Get the oncology drug (e.g. Cisplatin)
        result = await db.execute(select(Drug).filter_by(category="Oncology"))
        oncology_drugs = result.scalars().all()
        
        if not oncology_drugs:
            print("No oncology drugs found. Please run seed_database.py first.")
            return

        oncology_drug = oncology_drugs[0]
        
        # We need an inventory_id starting from the last max id or just let it generate? 
        # The schema has `inventory_id` as String, manually generated like f"INV{count:07d}"
        res_inv = await db.execute(select(DrugInventory.inventory_id).order_by(DrugInventory.inventory_id.desc()).limit(1))
        last_inv = res_inv.scalar()
        if last_inv and last_inv.startswith("INV"):
            inv_count = int(last_inv[3:])
        else:
            inv_count = 0

        for month in range(1, 13):
            inv_count += 1
            inv_id = f"INV{inv_count:07d}"
            batch_num = "BAT-" + "".join(random.choices(string.ascii_uppercase, k=4)) + "-" + "".join(random.choices(string.digits, k=4))
            
            expiry = date(2026, month, 15)
            manufacture = date(2025, 1, 1)
            
            new_batch = DrugInventory(
                inventory_id=inv_id,
                drug_id=oncology_drug.drug_id,
                batch_number=batch_num,
                manufacture_date=manufacture,
                expiry_date=expiry,
                current_stock=100,
                reorder_threshold=50,
                max_stock_level=500,
                average_daily_consumption=2.5,
                supplier_lead_time_days=10,
                last_restocked=date(2025, 6, 1)
            )
            db.add(new_batch)
            print(f"Added batch {batch_num} for {oncology_drug.drug_name} expiring on {expiry}")
            
        await db.commit()
        print("Successfully added 12 high-value oncology batches expiring in 2026.")

if __name__ == "__main__":
    asyncio.run(add_2026_expiry_drugs())
