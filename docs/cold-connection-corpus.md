# D42: cold connection → prospective окна → execution uncertainty

2026-10-03. [План](../configs/cold-connection-v1.json) фиксируется до новых запросов.
D41 closed artifact не возобновляется. WSS off, без QuickNode, D33, оплаты, сделок и ML.

Четыре новых процесса/соединения, порядок Alchemy/public/public/Alchemy. Между первым
и вторым раундом минимум60 s. До двух strict finalized/profile2 checkpoints одного
фиксированного pool на соединение: первый cold, второй проверяет фактический reuse.
Первая ошибка завершает trial, максимум3 failed trials на всю диагностику. Timeout15s
не увеличивается; parent завершает зависший child через35s. Нет повторов RPC внутри
trial, адреса по DNS выбирает прежний stdlib алгоритм; его отдельные TCP attempts
регистрируются, не объявляются RPC retries. Максимум8 RPC, новых transport вариантов нет.

Тонкие wrappers фиксируют start/end DNS, TCP connect, TLS wrap/handshake и safe exception
class/errno, не сохраняют URL/key/exception message. Они вызывают прежние функции
без изменения аргументов; TLS verification остаётся. Raw captures и phase journal
сохраняются до обработки. Это observer-only instrumentation отдельного инструмента,
а не изменение production transport. DNS может блокировать дольше socket timeout;
внешний child deadline ограничивает диагностический процесс. TCP/TLS failures не
доказывают upstream причину, billing/rate limits или неверные credentials.

Кандидат для corpus должен завершить последний trial двумя strict snapshots. Выбор
по числу полных trials, tie→Alchemy; иначе corpus сеть не запускается и выполняется
offline uncertainty на прежних данных. Выбор bounded operational mode не означает SLA.
Никаких новых циклов tuning после этой диагностики.

Два prospective окна на выбранном source, каждый со своим immutable plan/cutoff:
2×30 rounds×5s, максимум180 requests/окно,360 всего. Старт второго не ранее600s после
первого, вне зависимости от доходности/успешности первого. Ошибка закрывает только
текущее окно; нет resume failed segment/fallback. Новая invocation/TLS на сегмент.
Каждый gap честно отсутствует в data; окна не склеиваются. Десять минут разнесения не
объявляются разными market regimes. Actual calendar bounds обязательно в evidence.

Existing targets/quality/nonoverlap QA плюс offline uncertainty: conditional outcomes
до неизвестных network costs, break-even additional roundtrip cost в input-asset bps,
сетка0/5/10/25/50/100bps — только assumptions, не реальные тарифы. Сравнить paired delay0/5s
на тех же decisions/direction/size/partition; censored/unfilled не превращаются в нули.
Не смешивать atomic units разных assets, не суммировать overlapping hypothetical PnL.
Unknown admission/inclusion/FX/rent/persistent own impact сохраняются; net_pnl=null.
Проверить artifacts/source/offline/wheel; seed null. Обучение не начинается.

Официальная семантика: [HTTPSConnection](https://docs.python.org/3.13/library/http.client.html#http.client.HTTPSConnection),
[socket.create_connection](https://docs.python.org/3.13/library/socket.html#socket.create_connection).
Результат пока не получен.
