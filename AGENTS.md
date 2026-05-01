# AGENTS.md — pointer to canonical FOREX AI 2026 system

Этот репозиторий — **зеркало/резерв**. Канонический рабочий код находится в
**`Jony-wws/Forex-wws2277`**.

## Если ты Devin и читаешь это после «продолжай» / «continue»

1. Не делай ничего в этом репо.
2. Перейди в канонический репо:

   ```bash
   git clone https://github.com/Jony-wws/Forex-wws2277.git ~/repos/Forex-wws2277
   cd ~/repos/Forex-wws2277
   git checkout devin/1777586006-teamagent-rebuild
   git pull
   ```

3. Прочитай `AGENTS.md` и `SESSION_STATE.md` в этом каноническом репо.
4. Запусти систему: `bash scripts/start_all.sh`.
5. Открой дашборд наружу: `deploy expose port=8080`.
6. Передай пользователю URL/логин/пароль.

## Что это за проект

Multi-agent paper-trading система для 28 валютных пар Forex.
- Real data only (Yahoo, Dukascopy, ForexFactory RSS).
- Никаких симуляторов / random / fake.
- Strategy search: 90 вариантов × 4 сессии × 28 пар, 60-дневный бэктест.
- Реальный 70% WR гейт для открытия сделок (per-session).

## Полная история и контекст

См. `SESSION_STATE.md` рядом с этим файлом — там полный слепок состояния
системы, известные ограничения, план на будущие сессии и команды-чек-лист.

PR с историей всех изменений: <https://github.com/Jony-wws/Forex-wws2277/pull/1>
