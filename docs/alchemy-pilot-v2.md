# D40-v2: matched cadence и роль WSS

План заморожен до сети: [v2](../configs/alchemy-pilot-v2.json). V1 и его raw/source
сохранены; прежний pinned wheel воспроизводит v1. Три прежних пула, finalized/profile2.
Два процесса по 120 секунд: steady без fault, затем symmetric stress. Никакого ML,
подписи, торговли, оплаты/upgrade, QuickNode или D33 historical audit.

Четыре Alchemy варианта, отдельные persistent HTTP соединения:

- `http5`: фиксированная абсолютная сетка 5 s.
- `http10`: фиксированная абсолютная сетка 10 s.
- `wss10`: та же 10 s сетка, WSS как дополнительный raw/диагностический канал.
- `adaptive`: eligibility на общей 5 s сетке, changed-content trigger, heartbeat 10 s.

`http10`/`wss10` — строго matched-cadence пара. Adaptive сравнивается как другая
sampling policy с двумя fixed-cadence controls, не как чистый эффект транспорта.
Общая 5 s сетка features, одна и та же temporal math, вращение порядка запросов.
Deadline вычисляется от старта, а не от завершения RPC. Overrun >4 s прекращает
сегмент без catch-up burst. Raw identical-content notifications сохраняются, но
новый slot при том же value не вызывает новый HTTP. Late/same-slot ambiguity остаётся
консервативным hint; HTTP anchors не вытесняют уже полученный более новый hint.

В stress все четыре arm получают gap на tick12, закрывают HTTP, пропускают read;
WSS также disconnect/resubscribe. На tick13 у всех новый recovery checkpoint,
дальше прежняя сетка. Глобальный gap не закрывает прошлую историю, warmup обязательный.
Public D39 collector остаётся отдельным неизменённым contextual control.

Предварительный критерий для adaptive: минимум20% меньше HTTP, чем http5, ухудшение
usable fraction не более5 п.п., не меньше economic changes, чем http10; дополнительно
нужно ≥20% меньше полного payload относительно http10 ИЛИ ≥20% больше наблюдённых
economic changes при payload не выше http5. При отсутствии этого evidence WSS остаётся
опциональной диагностикой, выключенной по умолчанию. Steady и stress не смешиваются.
Counts changes не объявляются полным trade flow; мало/ноль активности не доказывает
равенства источников. Все budgets/stop rules закреплены в JSON.

Следующий этап выбирается по результату, с отдельным решением в Memory Bank. Длинный
corpus, coverage и execution uncertainty остаются gates перед обучением.
