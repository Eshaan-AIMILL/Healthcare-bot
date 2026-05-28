import asyncio
from app.tools.text2sql import run_text2sql
from app.core.security import SecurityContext
from app.core.rbac import Role
from app.db.session import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as db:
        ctx = SecurityContext(user_id='1', email='test@test.com', openwebui_role='user', enterprise_role=Role.ADMIN, session_id='1', timestamp=0, signature='')
        rows = await run_text2sql('Give me a risk dashboard across billing, compliance, pharmacy, patient and dispatch', db, ctx, domain='billing')
        print(rows)

asyncio.run(main())
