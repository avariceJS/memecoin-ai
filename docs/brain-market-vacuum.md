# Market vacuum: поэтапная квалификация после forensic review

2026-10-06. [Исходные измерения](brain-experience-review.md).
Это новый exploratory Brain track, не изменение frozen B13 или F01.

## Протокол до новых provider RPC

Сначала Alchemy indexed full pages по PumpSwap program, по2 страницы limit100 из каждого
заранее выбранного historical интервала, ascending: 2025-04-01, 2025-07-01,
2026-01-01, 2026-09-01 (каждый день UTC). Failure status any; никаких сегодняшних winners.
Начальный bound:8pages+до4standard hydration cross-check, ≤960 nominal CU/12RPC,
10MiB/response,1s pacing,300s deadline,USD0. Первое provider/schema/integrity failure
закрывает campaign, сохраняет reservation/STOP. Повторы в том же root запрещены.
Raw availability = actual receipt, historical blockTime никогда не online availability.
Транзакции с program reference ещё не swaps/births; неподдержанные layouts quarantine.

Основание no-paid — сохранённое owner attestation и exact credential fingerprint из B11,
не MCP usage delta (снова немонотонна, теперь0). Helius MCP подтверждает configured key,
но plan unknown: не выполнять signup/upgrade или считать доступ verified.
GT live клиент сохраняется; параллельный обход его frozen limiter не допускается.

После первых pages: сравнить decoded useful events/CU/bytes, venue/profile coverage и
creation incidence. Если приемлемо, отдельный versioned масштабный envelope до100000CU,
≤1000pages,≤10GiB raw с остановкой при low usefulness и без обхода source errors.
Это потолок разрешённого исследования, не обязательство выкачать quota.
Каждый расширенный campaign должен связать exact addresses/date-ranges/decoder pins
до RPC. Не обещать year coverage по четырём малым samples.

## Архитектурные границы

Provider adapter → immutable capture → journal cursor/gap debt → versioned decoder →
canonical events/market birth registry → sealed shards → dataset → fixed-recipe candidate.
Один program page маршрутизируется во все встретившиеся pools; signature+instruction path
задаёт identity. Неизвестный decoder и conflicting revision не продвигают admission.
Failed transactions сохраняются отдельно и не становятся executed swaps.
Дедупликация global; restart не обнуляет budget; pending reservation не бесплатный retry.
Read-only tracking конкретного pool/mint — адресный range этого же пути, не Axiom API.

Canonical unit/quote namespace обязателен: WSOL prices не смешивать с GT USD bars.
Сначала поддержанный economic profile и проверка gaps; затем новые train cohorts.
Данные birth registry сохраняют и недавние, и исчезнувшие markets; отсутствие нынешнего
account не считается rug. Migration-only выборка имеет graduation bias, который надо
показать рядом с survivorship bias. Protocol support не означает execution calibration.

Multi-Mac позже: immutable shard/model transfer и versioned API; один SQLite writer
на store. LLM node может читать incident/corpus summaries, но не менять модели, policy
или admission. Его установка сейчас не приоритет.
