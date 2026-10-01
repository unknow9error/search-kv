import asyncio
from datetime import timedelta

from app.domain.models import utcnow
from app.domain.schemas import ListingInput, Snapshot


class DemoProvider:
    demo = True

    def __init__(self, suffix: str, delay: float):
        self.id = "demo-" + suffix
        self.name = {"garden": "Пример: Садовые кварталы", "river": "Пример: Дом у реки"}[suffix]
        self.cities = ["Астана", "Алматы"]
        self.delay = delay
        self.offset = 0 if suffix == "garden" else 1

    def records(self) -> list[ListingInput]:
        items = []
        for city_index, city in enumerate(self.cities):
            for index in range(6):
                latitude = (
                    (51.1210 if city == "Астана" else 43.2300) + index * 0.003 + self.offset * 0.01
                )
                longitude = (71.4150 if city == "Астана" else 76.9200) + index * 0.002
                rooms = index % 3 + 1
                items.append(
                    ListingInput(
                        external_id=f"{city_index}-{index}",
                        city=city,
                        complex_name="Садовые кварталы" if self.offset == 0 else "Дом у реки",
                        district="Нұра" if city == "Астана" else "Бостандыкский",
                        address="Демонстрационный адрес",
                        rooms=rooms,
                        area_m2=round(31.5 + rooms * 12 + index * 1.3, 1),
                        floor=index + 2,
                        total_floors=12,
                        price_kzt=19_000_000
                        + rooms * 5_000_000
                        + index * 800_000
                        + self.offset * 1_400_000,
                        status="available",
                        latitude=latitude,
                        longitude=longitude,
                        finish="Предчистовая",
                        completion="IV квартал 2027",
                        source_url="https://example.com/demo-apartment",
                        amenities=[
                            {
                                "kind": kind,
                                "state": "operating",
                                "name": name,
                                "latitude": latitude + delta,
                                "longitude": longitude,
                                "source_url": "https://example.com/demo-places",
                                "observed_at": utcnow() - timedelta(days=1),
                            }
                            for kind, name, delta in [
                                ("school", "Пример школы", 0.004 + index * 0.001),
                                ("kindergarten", "Пример детского сада", 0.002 + index * 0.001),
                                ("park", "Пример парка", 0.006),
                            ]
                        ],
                    )
                )
        return items

    async def fetch(self, city=None):
        await asyncio.sleep(self.delay)
        return Snapshot(
            as_of=utcnow(),
            complete=True,
            items=[x for x in self.records() if city is None or x.city == city],
            scope_city=city,
        )

    async def verify(self, external_id):
        await asyncio.sleep(self.delay)
        return Snapshot(
            as_of=utcnow(),
            complete=False,
            items=[x for x in self.records() if x.external_id == external_id],
        )
