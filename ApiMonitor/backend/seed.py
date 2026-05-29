import asyncio
from database import SessionLocal, engine
from models import Endpoint, Base

async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    apis = [
        {"nome": "Cat Facts", "url": "https://catfact.ninja/fact"},
        {"nome": "Coindesk BPI", "url": "https://api.coindesk.com/v1/bpi/currentprice.json"},
        {"nome": "Bored API", "url": "https://www.boredapi.com/api/activity"},
        {"nome": "Agify", "url": "https://api.agify.io?name=michael"},
        {"nome": "Genderize", "url": "https://api.genderize.io?name=lucia"},
        {"nome": "Nationalize", "url": "https://api.nationalize.io?name=nathaniel"},
        {"nome": "Data USA", "url": "https://datausa.io/api/data?drilldowns=Nation&measures=Population"},
        {"nome": "Dog CEO", "url": "https://dog.ceo/api/breeds/image/random"},
        {"nome": "IPInfo", "url": "https://ipinfo.io/161.185.160.93/geo"},
        {"nome": "Random User", "url": "https://randomuser.me/api/"},
        {"nome": "Universities", "url": "http://universities.hipolabs.com/search?country=United+States"},
        {"nome": "Zippopotam", "url": "http://api.zippopotam.us/us/33162"},
        {"nome": "Github Status", "url": "https://www.githubstatus.com/api/v2/status.json"},
        {"nome": "Discord Status", "url": "https://discordstatus.com/api/v2/status.json"},
        {"nome": "Reddit Technology", "url": "https://www.reddit.com/r/technology.json"},
        {"nome": "ReqRes Users", "url": "https://reqres.in/api/users?page=2"},
        {"nome": "Open-Meteo", "url": "https://api.open-meteo.com/v1/forecast?latitude=52.52&longitude=13.41&current_weather=true"},
        {"nome": "Rick and Morty", "url": "https://rickandmortyapi.com/api/character"},
        {"nome": "SWAPI", "url": "https://swapi.dev/api/people/1/"},
        {"nome": "Anime Chan", "url": "https://animechan.xyz/api/random"}
    ]

    async with SessionLocal() as db:
        for api in apis:
            ep = Endpoint(nome=api['nome'], url=api['url'], intervalo_minutos=1)
            db.add(ep)
        await db.commit()

    print("20 APIs added successfully!")

if __name__ == "__main__":
    asyncio.run(seed())
