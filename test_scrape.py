import httpx
import asyncio

async def main():
    async with httpx.AsyncClient() as c:
        r = await c.get('https://t.me/durov', headers={
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_8 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.2 Mobile/15E148 Safari/604.1',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7'
        })
        with open('durov_mobile.html', 'w', encoding='utf-8') as f:
            f.write(r.text)

asyncio.run(main())
