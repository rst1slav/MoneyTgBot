import httpx
import asyncio

async def main():
    async with httpx.AsyncClient() as c:
        r = await c.get('https://gift-satellite.dev/api/docs.md', headers={'User-Agent': 'Mozilla/5.0'})
        print(r.status_code)
        print(r.text[:2000])

asyncio.run(main())
