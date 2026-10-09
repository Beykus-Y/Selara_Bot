# Games UX 2.0 — per-game phase matrix (GUX-00 DoD)

Source: `dev` at a9bc99d, read from code. Every row cites file and line. Nothing here comes from a device run or a screenshot.
Abbreviations: **R** = `src/selara/presentation/handlers/game/router.py`, **S** = `src/selara/presentation/game_state.py`, **W** = `src/selara/web/app.py`.
Alert texts are the Russian strings from the code. Button labels omit their leading emoji.

Callback prefixes: `game:*` (generic), `gcfg:` (lobby config), `gquiz:`, `gdice:`, `gspy:`, `gwho:`, `gbredcat:`, `gbred:`, `gzlobv:`, `gzlobp:`, `gbkr:`, `gbkv:`, `gmact:`, `gmvote:`, `gmconfirm:`.
"manage" means the `manage_games` permission, checked by `_actor_can_manage_games` (R:137).

GamePhase (S:29-44) has 14 values. All 14 are used by the 8 games. `freeplay` is shared by spy, dice and quiz with different meanings. `private_answers` and `public_vote` are shared by zlobcards and bredovukha.

---

## 1. Shared mechanics

| Item | Code |
|---|---|
| Group board builder | `_build_game_controls` R:2097 (lobby branch R:2100-2162; started footer R:2229-2239; finished R:2170-2171). Board text `_render_game_text` R:1819-2015, phase titles R:891-920. |
| Started footer, visible to everyone | "Как играть" `game:lrules` (R:2233). During play it sends the rules as a new message (R:4609-4619). "Ведущему" `game:manage` (R:2234). |
| Manager panel | `game:manage` R:4652-4672 posts `_build_game_manager_controls` R:2055-2094 into the group. Anyone can see it. Each press is gated by manage (R:4639-4650), toast "Недостаточно прав для управления игрой." (show_alert=False). |
| Versioned advance `game:adv:{gid}:{phase}:{round}` | Stale check R:4473-4482, alert "Фаза игры изменилась. Найдите текущую доску через /gameboard." |
| Stop and reveal (spy, mafia) | Stop: `game:cancel` R:5095-5107 posts a confirm message. Reveal: `game:reveal` R:5079-5093 (spy only, R:5028-5030). Confirm `game:sok`, `game:rok`, `game:back` R:4998-5077. Keyboard R:2038-2052. Expiry 180 s and expected phase and round check R:5019-5027, alert "Игра изменилась или подтверждение истекло. Вернитесь к доске через /gameboard." Finish: `GameStore.finish` S:2085-2118 with expected status, phase and round. |
| Lobby join | R:4502-4517. S:1767-1783 returns `already_joined` (R:4508-4510 "Вы уже в игре") or `not_lobby` (R:4511-4513 "Нельзя присоединиться: игра уже запущена"). No player cap. |
| Lobby leave | R:4519-4540. S:1785-1798. Owner refused, alert R:4531-4535 "Создатель лобби не может выйти — отмените игру кнопкой «🛑 Отменить», если она больше не нужна." |
| Lobby rules and back | `game:lrules` R:4609-4632 (edits board; back button `game:lback` R:574-578, handler R:4634-4637). |
| Start | R:4674-4770. Permission: owner or manage, `_actor_can_start_game` R:162-180, alert R:4683-4685 "Старт может нажать создатель лобби или участник с правом управления играми." GameStore errors as alert R:4694-4696. Min-player error S:1809-1810 "Для старта нужно минимум N игроков". Already started S:1807-1808. |
| Lobby cancel | `game:cancel` R:5108-5123 (also manage-gated R:4639-4650). `GameStore.finish` with expected lobby S:2112-2118. Text "Лобби отменено ведущим.". |
| Finished rematch | `game:rematch` R:2170-2171. Handler R:4542-4607, manage gate R:4548-4558, alert "Недостаточно прав для запуска игр в этом чате.". Settings carry over R:4590-4601. |
| Role DMs at start | R:4701-4703 for spy, mafia, bunker, whoami, zlobcards. `_send_role_to_user` R:3014-3111 (keyboard only for bunker and mafia-night cases). |
| Phase timers | Only mafia and zlobcards. `_schedule_phase_timer` R:2699-2831 (early return R:2705-2706 for other kinds). Stale check `_is_stale_timer` R:2713-2723. Cancel R:2693-2696. Restore after restart R:2834-2913. |

---

## 2. Per-game matrices

### 2.1 zlobcards (500 Злобных Карт)

Phases: lobby, private_answers, public_vote, finished. Timers: private_answers and public_vote, 75 s each (R:85-86).

| Phase | Visible keyboard | Actor | Surface | Error (refusal) | Transition |
|---|---|---|---|---|---|
| lobby | Group: join `game:join`, leave `game:leave`, rules, "Тема" `gcfg:{gid}:zlob_cat_next` (R:2137-2142), rounds stepper `zlob_rounds_dec/noop/inc` (R:2143-2146), target stepper `zlob_target_dec/noop/inc` (R:2147-2150), start `game:start`, cancel `game:cancel` (R:2157-2158). | Join, leave, rules: anyone. Config and cancel: manage (R:4074-4084, R:4639-4650). Start: owner or manage (R:4674-4686). | Group | Toast R:4160-4162 "Настройку можно менять только в лобби «500 Злобных Карт»". Start: S:1809-1810. Others as section 1. | Start: S:1886-1941. `_prepare_zlob_private_phase` S:1938 sets private_answers. Start timer R:4732. Cancel: S:2112-2118. |
| private_answers | Group: URL "Рука в ЛС" (R:2182-2183), manager "Открыть голосование" `game:adv` (R:2079-2080). DM: one `gzlobp:{gid}:{round}:{idx}` per hand card (1 slot), or `gzlobp:{gid}:{round}:{first}-{second}` per pair (2 slots), plus "Обновить" `gzlobp:…:noop` (R:790-820). Sent by `_send_zlob_hand_to_user` R:2535-2547, refreshed R:5561-5564, R:5611-5614. | DM: any player with a hand (R:790-794, S:3203-3204). Manager: manage. | DM (submit); group (URL, manager, progress) | DM alert R:5598-5600 with S:3182-3247: "Сейчас не этап приватного выбора" (S:3202), "Чёрная карточка текущего раунда не выбрана" (S:3206), "Нужно выбрать карточек: N" (S:3210), "Для раунда с двумя пропусками нужны две разные карты" (S:3212), "Ваша рука пуста" (S:3216), "Некорректная карта в выборе" (S:3221). Stale: R:5517 "Это старая рука. Откройте /role для актуальных карт.", R:5535 "Эта рука уже неактуальна. Откройте /role.", S:3197-3198 "Кнопка предыдущего раунда. Откройте актуальную доску.". | (a) All players submitted: S:3231-3235, `_open_zlob_vote` S:5169-5193 sets public_vote. Telegram handler R:5619-5628 cancels the timer and does NOT schedule the vote timer (divergence D2). (b) Timer 75 s R:2725-2747 calls `_open_zlob_vote_phase` R:3573-3596, which schedules the vote timer R:3593. (c) Manager "Открыть голосование" R:4859-4876, forced, `zlob_open_vote` S:3257-3280. |
| public_vote | Group only: options `gzlobv:{gid}:{round}:{idx}` labelled A., B., … (R:770-787), progress `gzlobv:…:noop`. Manager "Закрыть раунд" `game:adv` (R:2081-2082). DM: none (R:2357-2359). | Any player except the author of the option (S:3308-3314). Non-players refused S:3303-3304. | Group | Alert R:5671 "Этот раунд закрыт. Обновите /gameboard."; S:3302 "Сейчас не этап голосования"; S:3304 "Вы не участник этой игры"; S:3314 "Нельзя голосовать за свою карточку" (alert R:5726-5728); S:3306 "Некорректный вариант". | (a) All voted: R:5749-5762 cancels timer (R:5750), then `_resolve_zlob_round` R:3599-3647 and `zlob_resolve_round` S:3350-3491. Next round sets private_answers (S:3454-3457) and schedules the private timer (R:3644). Ends when round ≥ zlob_rounds or top score ≥ target (S:3433-3449). (b) Timer 75 s R:2749-2769. (c) Manager "Закрыть раунд" R:4878-4893, forced. |
| finished | Rematch `game:rematch` (R:2170-2171). Board shows winner text only (R:1963 limits the round block to started). | Manage (R:4548-4558). | Group | Alert R:4557 "Недостаточно прав для запуска игр в этом чате."; R:4543-4545 "Эта игра ещё не завершена". | Terminal (S:3447-3449, or S:3465-3472 when no black cards remain). Feed R:3627-3631. |

### 2.2 spy (Найди шпиона)

Phases: lobby, freeplay, finished. Timers: none.

| Phase | Visible keyboard | Actor | Surface | Error (refusal) | Transition |
|---|---|---|---|---|---|
| lobby | Join, leave, rules, "Тема" `gcfg:{gid}:spy_cat_next` (R:2125-2130), start, cancel. | Category toggle: manage (R:4295-4310, gate R:4074-4084). Others as section 1. | Group | Toast R:4296-4298 "Настройку можно менять только в лобби «Шпиона»". Start: S:1809-1810; S:1825 "Не удалось загрузить банк локаций для игры «Шпион»"; S:1831 "В теме «…» не осталось доступных локаций для игры «Шпион»". | Start S:1823-1842 → freeplay (S:1840). Role DM with location or spy text, no keyboard (R:3083-3090). |
| freeplay | Group: per-player vote `gspy:{gid}:{uid}` "name · votes", sorted by name (R:679-690); "Мой голос" `gspy:{gid}:mine` (R:693); progress `gspy:{gid}:noop` (R:694). Footer "Сводка голосов" `gspy:{gid}:noop` (R:2167-2168). Manager "Раскрыть роли" `game:reveal` (R:2088-2089), "Завершить" `game:cancel` (R:2091). DM: none. | Any player (S:2292-2293). Non-players see buttons and are refused. Reveal and cancel: manage. | Group (vote); DM role card only | "Эта кнопка из другого чата" (alert R:6074-6076). "Мой голос" (R:6077-6092): "Партия уже завершена. Откройте актуальную доску." (R:6079), "Вы не участник этой игры." (R:6082-6083), "Вы ещё не голосовали. Выберите подозреваемого." (R:6086). Vote refusals S:2290-2295, alert R:6124-6126: "Игра уже завершена или неактивна", "Вы не участник этой игры", "Такого игрока нет в этом лобби". Reveal outside spy: toast R:5080-5081. | Vote: `spy_register_vote` S:2274-2352. Auto-finish when everyone has voted or the top count reaches a majority (S:2315-2337). Caught spy means the civilians win; a tie or a wrong pick means the spy wins (S:2325-2334). Manual reveal: `game:rok` confirm R:5079-5093, then finish R:5035-5047, roles shown R:5065-5068. Cancel: `game:sok` R:5095-5107. Mini App only: spy guess location (section 4). |
| finished | Rematch (R:2170-2171). Board shows roles via include_reveal (R:5067). | Manage (R:4548-4558). | Group | Alert R:4557. | Terminal. |

### 2.3 whoami (Кто я)

Phases: lobby, whoami_ask, whoami_answer, finished. Timers: none.

| Phase | Visible keyboard | Actor | Surface | Error (refusal) | Transition |
|---|---|---|---|---|---|
| lobby | Join, leave, rules, "Тема" `gcfg:{gid}:whoami_cat_next` (R:2131-2136), start, cancel. | Category toggle: manage (R:4255-4273). | Group | Toast R:4256-4258 "Настройку можно менять только в лобби «Кто я»". Start: S:1854 "18+ темы для игры «Кто я» отключены в этом чате"; S:1860 "В категории «…» недостаточно карточек для N игроков". | Start S:1844-1884 → whoami_ask (S:1882). Role DM is the private card view (R:2652-2680), no keyboard (R:3056-3067). |
| whoami_ask | Group: footer and URL "Карточки" (R:2178-2179). No vote buttons. DM: none. Input is free text in the group from the current turn player. | Current turn player only. Routing R:1004-1021. Non-current texts are skipped silently (R:6467). Solved players refused (S:2447-2448). | Group (text input) | Replies, not alerts: R:6517-6522 hint when there is no "?". Question: S:2440-2442 "Сейчас нельзя задавать вопрос", S:2446 "Сейчас ход другого игрока", S:2452 "Вопрос слишком короткий", S:2454 "Вопрос должен быть короче 180 символов". Guess: S:2570 "Сейчас нельзя делать догадку", S:2580 "Введите догадку", S:2582 "Догадка должна быть короче 120 символов". Replies R:6490-6492, R:6529-6531. | Question: `whoami_submit_question` S:2428-2472 sets whoami_answer (S:2461). Correct guess with all solved: finished (S:2612-2622). Correct guess otherwise, or wrong guess: next turn, phase stays whoami_ask (S:2627-2637). |
| whoami_answer | Group: "Да" `gwho:{gid}:{rev}:yes`, "Нет" `…:no`, "Не знаю" `…:unknown`, "Неважно" `…:irrelevant` (R:709-722). `rev` is the phase-start timestamp (R:699-706). DM: none (R:2672-2674 says answer in the group or on the site). | Any registered player except the asker (S:2501-2502). Asker refused (S:2508-2509). | Group | Alert R:6003 "Этот вопрос устарел. Откройте /gameboard."; S:2496 "Это старый вопрос. Откройте актуальную доску."; S:2500 "Сейчас нет активного вопроса"; S:2502 "Вы не участник этой игры"; S:2509 "Игрок, задавший вопрос, не может отвечать сам себе". | `whoami_answer_question` S:2474-2554. "Да" keeps the asker's turn (S:2525-2531). Any other answer advances the turn (S:2533-2537). Both return to whoami_ask. |
| finished | Rematch (R:2170-2171). Board shows history and roles (R:1909-1914). | Manage (R:4548-4558). | Group | Alert R:4557. | Terminal. |

### 2.4 mafia (Мини-мафия)

Phases: lobby, night, day_discussion, day_vote, day_execution_confirm, finished. Timers on the four middle phases. Durations come from chat settings: night R:2775, day discussion R:2790, day vote R:2805, execution confirm R:2820.

| Phase | Visible keyboard | Actor | Surface | Error (refusal) | Transition |
|---|---|---|---|---|---|
| lobby | "Роль выбывшего: вкл/выкл" `gcfg:{gid}:reveal_elim` (R:2116-2119; handler R:4096-4124, toast "Режим: показывать/скрывать"). Plus join, leave, rules, start, cancel. | Toggle: manage. Others as section 1. | Group | R:4097-4099 "Настройку можно менять только в лобби мафии". Start min 4 (S:1809). | Start: S:1943-1987 assigns roles (`_assign_mafia_roles` S:5445), sets night (S:1946). First night DMs R:4707 → R:3122-3158; start timer R:4706. |
| night | Group: URL "Моя роль" and "Ночной ход" (R:2174-2177). Manager "Завершить ночь" `game:adv` (R:2062-2063, R:2070). DM: `gmact:{gid}:{round}:{uid}` "🎯 name" per target (R:870-888), sent R:3122-3158, refreshed R:6242-6243. Roles without a night action get no keyboard (R:3095-3098). | DM: alive players with a night role (S:3791-3794). Targets exclude eliminated players (S:4918-4919) and follow per-role rules (S:4915-4981). Group press: manage only. | DM (actions); group (manager and URLs) | Alert R:6216-6217 from `mafia_register_night_action` S:3773-3941: "Кнопка предыдущего раунда. Обновите игровую доску." (S:3786), "Сейчас не фаза ночи" (S:3790), "Вы выбиты и не можете делать ход" (S:3792), "Цель уже выбыла" (S:3802, 3828, 3859, 3894, 3912), "Нельзя выбрать себя" (S:3804), "Мафия не может атаковать своих" (S:3806), "Нужно выбрать другого живого игрока" (S:3816 and similar), "Для сравнения выберите второго игрока" (S:3852), "Боевая готовность уже была использована" (S:3867), "Реанимация уже использована" (S:3874), "Зелье спасения уже использовано" (S:3915), "Зелье убийства уже использовано" (S:3919), "Ребёнок может раскрыться только сам" (S:3937), "У вашей роли нет ночного действия" (S:3941). Group press R:6202-6204 "Ночные действия только в ЛС.". | (a) All required roles acted: `mafia_is_night_ready` S:3943-4008, auto-advance R:6248-6253. (b) Timer `mafia_night_seconds` R:2774-2787. (c) Manager R:4959-4963. `_advance_mafia_night` R:3203-3278 calls `mafia_resolve_night` S:4061-4621. Result: day_discussion (S:4603), or finished on mafia win (S:4595-4601). |
| day_discussion | Group: manager "Открыть голосование" `game:adv` (R:2064-2065). No vote buttons. DM: none. | Manager only. | Group | Toast R:4649-4650 for non-managers. Versioned stale alert R:4481. S:4017-4018 "Сейчас не обсуждение дня". | Timer `mafia_day_seconds` R:2789-2802 → `_open_mafia_day_vote` R:3280-3310 → `mafia_open_day_vote` S:4010-4028 (day_vote, S:4024). Manual: R:4965-4969. Vote DMs R:3306, timer R:3307. |
| day_vote | Group: `gmvote:{gid}:{round}:{uid}` for ALL alive players including self (R:581-598). DM: `gmvote` for alive players except self and the advocate-protected target, with a checkmark on the current pick (R:601-626). DM sent R:2410-2435 at open, refreshed R:6327-6344. Manager "Подвести голоса" `game:adv` (R:2066-2067). | Alive players only (S:4048-4049). Self-vote (S:4052-4053) and immune target (S:4054-4055) are refused, even though the group board shows them (divergence D4). | Both | Alert R:6296-6298 from `mafia_register_day_vote` S:4030-4059: "Кнопка предыдущего раунда. Обновите игровую доску." (S:4042-4043), "Сейчас не фаза голосования" (S:4046-4047), "Вы выбиты и не можете голосовать" (S:4049), "Цель уже выбыла" (S:4051), "Нельзя голосовать против себя" (S:4052-4053), "Этого игрока сегодня прикрыл адвокат" (S:4054-4055). Chat mismatch R:6282-6287. | All alive voted: R:6346-6356 → `_resolve_mafia_day_vote`. Timer `mafia_vote_seconds` R:2804-2817. Manager R:4971-4981. `mafia_resolve_day_vote` S:4623-4704: a candidate moves to day_execution_confirm (S:4667-4672; timer R:3354; confirm message R:3353). A tie or no votes moves to night, round+1 (S:4683-4689; timer R:3391; night DMs R:3392). Mafia win → finished (S:4674-4681). |
| day_execution_confirm | Separate group message, not the board: `gmconfirm:{gid}:{round}:yes` "Да (n)", `…:no` "Нет (n)", `…:noop` "v/a" (R:629-640, posted R:2364-2407). Board shows text only (R:1878-1887). Manager "Закрыть казнь" `game:adv` (R:2068-2069). DM: none. | Alive players (S:4724-4725). Manager for closing. | Group | Alert R:6382 "Подтверждайте казнь в исходной группе." (wrong chat). R:6384-6386 "Голосование завершено. Откройте /gameboard." (stale). S:4719-4723 "Сейчас не фаза подтверждения". S:4725 "Вы выбиты и не можете голосовать" (alert R:6413-6415). | All alive voted: R:6435-6445 → `_resolve_mafia_execution_confirm` R:3399-3489 → `mafia_resolve_execution_confirm` S:4745-4840. Passed iff yes > no (S:4779), so a tie does not pass. Candidate executed (S:4784-4788). Jester win S:4810-4811. Otherwise winner check, then finished (S:4814-4820) or night, round+1 (S:4822-4824; timer R:3484; DMs R:3485). Timer R:2819-2831. Manager "Закрыть казнь" R:4983-4993. |
| finished | Rematch (R:2170-2171). Board includes roles (R:3257-3264, R:3467-3475). | Manage (R:4548-4558). | Group | Alert R:4557. | Terminal. |

### 2.5 dice (Дуэль кубиков)

Phases: lobby, freeplay, finished. Timers: none.

| Phase | Visible keyboard | Actor | Surface | Error (refusal) | Transition |
|---|---|---|---|---|---|
| lobby | Join, leave, rules, start, cancel (R:2110-2159). No dice-specific control. | As section 1. | Group | Start min 2 (S:1809-1810). | Start S:1989-1993 → freeplay (S:1991). Feed R:4740-4742. |
| freeplay | Group: "Бросить" `gdice:{gid}:roll` (R:2165-2166). Footer. Manager: cancel only (R:2091). DM: none. | Any player who has not rolled (S:2691-2694). | Group | Alert R:5255-5257 from `dice_register_roll` S:2674-2723: "Вы уже бросили кубик в этом раунде" (S:2694), "Вы не участник этой игры" (S:2692), "Игра уже завершена" (S:2690). Chat mismatch R:5247-5249. Unknown action R:5239-5241. | Each roll S:2693-2701. When all players have rolled, the game finishes automatically (S:2704-2710), and rewards and feed are sent (R:5267-5285). |
| finished | Rematch (R:2170-2171). Final table R:1916-1923. | Manage (R:4548-4558). | Group | Alert R:4557. | Terminal. |

### 2.6 quiz (Викторина)

Phases: lobby, freeplay, finished. Timers: none. Each question is one freeplay cycle, 5 questions (QUIZ_ROUNDS R:126).

| Phase | Visible keyboard | Actor | Surface | Error (refusal) | Transition |
|---|---|---|---|---|---|
| lobby | Join, leave, rules, start, cancel. No quiz-specific control. | As section 1. | Group | Start min 2 (S:1809-1810). | Start S:1995-2007 → freeplay (S:2000). Question feed R:4744-4746, `_sync_quiz_feed_message` R:2308-2338. |
| freeplay (per question) | Group: "A. text" … "F. text" `gquiz:{gid}:{qidx}:{opt}` (R:650-676). Progress `gquiz:{gid}:{qidx}:noop` "n/N". Manager "Закрыть вопрос" `game:adv` (R:2071-2072). DM: none. | Any player (S:2744-2745 refuses non-players). | Group | Alert R:5138 "Кнопка устарела. Откройте текущую доску через /gameboard." (unversioned payload). Alert R:5157 "Вопрос уже закрыт. Найдите актуальную доску через /gameboard.". `quiz_submit_answer` S:2725-2772: "Викторина неактивна" (S:2743), "Вы не участник этой викторины" (S:2745), "Этот вопрос уже закрыт. Используйте актуальную доску." (S:2751), "Некорректный вариант ответа" (S:2757). Alert R:5186-5188. Manager without rights: R:4649-4650. | All players answered: R:5203-5212 → `_resolve_quiz_round` R:3492-3527 → `quiz_resolve_round` S:2782-2855. The next question stays freeplay (S:2834-2836). Last question: finished (S:2824-2832). Manager "Закрыть вопрос" R:4777-4793 (forced). An answer can be changed while the question is open (R:5215-5224, S:2759-2761). No timer. |
| finished | Rematch (R:2170-2171). Scoreboard R:1938-1940. | Manage (R:4548-4558). | Group | Alert R:4557. | Terminal. Question feed removed R:3517 and R:5059-5063. |

### 2.7 bredovukha (Бредовуха)

Phases: lobby, category_pick, private_answers, public_vote, finished. Timers: none.

| Phase | Visible keyboard | Actor | Surface | Error (refusal) | Transition |
|---|---|---|---|---|---|
| lobby | "Раундов" stepper `gcfg:{gid}:bred_rounds_dec/noop/inc` (R:2120-2124; handler R:4126-4157). Join, leave, rules, start, cancel. | Stepper: manage (R:4074-4084). | Group | R:4127-4129 "Настройку можно менять только в лобби «Бредовухи»". Start: S:2011 "Не удалось загрузить банк вопросов «Бредовухи»", S:2012-2013 "Раундов должно быть не меньше количества игроков: N". Min 3 (S:1809). Join raises rounds to the player count (S:1779-1780). | Start S:2009-2046 → category_pick (S:2020). Feed R:4748-4750. |
| category_pick | Group: category buttons `gbredcat:{gid}:{round}:{idx}` "name" (R:745-767), selector label `gbredcat:…:noop`. Manager "Случайная тема" `game:adv` (R:2073-2074). DM: none (non-selectors get status text R:2453-2454). | Current selector only (S:2222-2223). Manager for random pick. | Group | Alert R:5315-5317 "Выбор темы завершён. Откройте /gameboard."; R:5299-5300 "Тема прошлого раунда. Откройте /gameboard.". `bred_choose_category` S:2201-2242: "Тема из предыдущего раунда. Откройте актуальную доску." (S:2217), "Это не «Бредовуха»" (S:2219), "Сейчас не этап выбора категории" (S:2220-2221), "Сейчас категорию выбирает другой игрок" (S:2223), "Некорректная категория" (S:2225), "Для выбранной категории нет доступных вопросов" (S:2230; alert R:5365-5366). Manager random: S:2253-2254 "Нет доступных категорий". | Selector pick → private_answers (S:2240-2241), then question DMs to all (R:5375 → S:2476-2485, sent S:2462-2473). Manager random: `bred_force_pick_category` S:2244-2272 (R:4795-4821). No timer. |
| private_answers | Group: URL "Сдать ложь в ЛС" (R:2180-2181). Manager "Открыть голосование" `game:adv` (R:2075-2076). DM: no keyboard. Players send a plain-text lie to the bot (prompt R:2446-2452). | Any player via DM text (S:2872-2873 refuses non-players). Manager for early open. | DM (lie); group (manager, URL, progress R:6612-6617) | Replies via `message.answer`, not alerts, R:6583-6585 from `bred_submit_lie` S:2857-2917: "Сейчас не этап сбора ответов" (S:2870-2871), "Ответ слишком короткий (минимум 1 символа)" (S:2879), "Ответ слишком длинный (максимум 120 символов)" (S:2881), "Нельзя отправлять правильный ответ. Нужна правдоподобная ложь." (S:2885), "Такой вариант уже отправил другой игрок. Придумайте другой." (S:2891). Stale prompt R:6562-6564 "Это старый вопрос «Бредовухи». Откройте актуальный через /role.". Manager early open without all answers: S:2945-2946 "Ещё не все игроки прислали ответы" (bypassed when forced). | All players submitted: `_open_bred_vote` S:5300-5323 sets public_vote (S:5320), board updated R:6603-6610. Manager "Открыть голосование" R:4823-4839 (forced) → `bred_open_vote` S:2928-2951. No timer. |
| public_vote | Group: options `gbred:{gid}:{round}:{idx}` A., B., … (R:725-742), progress `gbred:…:noop` with leader text (R:5419-5446). Manager "Закрыть раунд" `game:adv` (R:2077-2078). DM: none (R:2455-2456). | Any player (S:2974-2975). Self-vote on own lie is accepted (S:2979-2980 has no owner check), but scoring removes the own vote from the fooled count (S:3069-3070). | Group | Alert R:5417 "Этот раунд закрыт. Обновите /gameboard."; R:5410-5415 "Эта кнопка из другого чата"; `bred_register_vote` S:2953-2993: "Кнопка предыдущего раунда. Откройте актуальную доску." (S:2969), "Сейчас не этап голосования" (S:2973), "Вы не участник этой игры" (S:2975), "Некорректный вариант" (S:2977; alert R:5471-5473). | All voted: R:5497-5505 → `bred_resolve_round` S:3014-3180. Next round: category_pick with the next selector (S:3129-3132). Finished when rounds are done (S:3097-3103), when there is no next selector (S:3112-3125), or when there are no category options (S:3142-3155). Manager "Закрыть раунд" R:4841-4854 (forced). |
| finished | Rematch (R:2170-2171). Final round text R:3547-3563 and feed R:3558-3563. | Manage (R:4548-4558). | Group | Alert R:4557. | Terminal. |

### 2.8 bunker (Бункер)

Phases: lobby, bunker_reveal, bunker_vote, finished. Timers: none.

| Phase | Visible keyboard | Actor | Surface | Error (refusal) | Transition |
|---|---|---|---|---|---|
| lobby | "Места" stepper `gcfg:{gid}:bunker_seats_dec/noop/inc` (R:2151-2155; handler R:4222-4253; setter S:1498-1517, S:1510 "Мест в бункере должно быть минимум 2"). Join, leave, rules, start, cancel. | Stepper: manage. | Group | R:4223-4225 "Настройку можно менять только в лобби «Бункера»". Start: S:2053-2058 "В «Бункере» должно быть минимум 2 места", "Мест в бункере должно быть меньше количества игроков". Min 6 (S:1809-1810 with GAME_DEFINITIONS R:92-98). | Start S:2048-2081. `_prepare_bunker_reveal_phase` S:5034-5047 sets bunker_reveal (S:5038). Role DM with card R:3016-3054. First reveal DM R:4752-4768 → R:2627-2634. |
| bunker_reveal | Group: URL "Действие в ЛС" (R:2184-2185). Manager "Пропустить ход" `game:adv` (R:2083-2084). DM, current actor only: hidden fields `gbkr:{gid}:{round}:{cursor}:{field}` "🃏 field" plus refresh `…:noop` (R:823-845). Sent R:2627-2634 and R:3031-3034. | Current reveal actor (S:3531-3532) who is alive (S:3529-3530). Manager skip: manage. | DM (reveal); group (board and manager skip) | Group press R:5790-5792 "Раскрывайте поля только в личке." (alert). Stale R:5793-5796 "Ход уже завершён. Откройте /role." (alert). `bunker_register_reveal` S:3508-3562: "Кнопка предыдущего раунда. Обновите игровую доску." (S:3522), "Этот ход уже завершён. Откройте актуальную карточку." (S:3524), "Сейчас не этап раскрытия" (S:3528), "Вы выбыли и не можете раскрывать карточку" (S:3530), "Сейчас раскрывается другой игрок" (S:3532), "Некорректная характеристика" (S:3534), "Эта характеристика уже раскрыта" (S:3542). Manager skip R:4898-4906 with S:3575-3576. | Each reveal: `_advance_bunker_reveal_cursor` S:5059-5075 → next actor DM (R:5875-5877). End of round: `_open_bunker_vote_phase` S:5050-5056 → bunker_vote; vote DMs R:5860-5864. Manager skip → `bunker_force_advance_reveal` S:3564-3611. No timer. |
| bunker_vote | Group: URL "Действие в ЛС" (R:2184-2185). Manager "Завершить голосование" `game:adv` (R:2085-2086). DM, all alive players: `gbkv:{gid}:{round}:{uid}` for other alive players (R:848-867), plus refresh `gbkv:…:noop`. Sent by `_notify_bunker_vote_private` R:2637-2649. | Alive players (S:3632-3633). Self-vote refused (S:3636-3637). Eliminated target refused (S:3634-3635). | DM (votes); group (board and manager close) | Alert R:5942-5944 from `bunker_register_vote` S:3613-3641: "Сейчас не этап голосования" (S:3630), "Вы выбыли и не можете голосовать" (S:3633), "Этот игрок уже выбыл" (S:3635), "Нельзя голосовать против себя" (S:3637). Stale: R:5893-5895 "Это действие устарело. Откройте /gameboard.". Round mismatch S:3625-3626. Chat id not checked (divergence D3). | All alive voted: R:5974-5984 → `_resolve_bunker_vote` R:3650-3727 → `bunker_resolve_vote` S:3673-3771. Eliminated players are removed. Finished if alive ≤ seats (S:3726-3741, text S:3735). Otherwise round+1 (S:3743): bunker_reveal if hidden fields remain (S:3744-3747; reveal DM R:3701), or bunker_vote (S:3751; vote DMs R:3705). Manager R:4934-4950 (forced). No timer. |
| finished | Rematch (R:2170-2171). Board shows winners, no reveal (R:3681-3686). | Manage (R:4548-4558). | Group | Alert R:4557. | Terminal. |

---

## 3. Timer-driven transitions

| Game | Phases with timer | Duration and source | Scheduled at | Phases with no timer |
|---|---|---|---|---|
| zlobcards | private_answers; public_vote | 75 s each (R:85-86); floor 5 s (R:2727, R:2750). | private: start R:4732, after round R:3644, restore R:2877-2879, R:2944-2945. vote: `_open_zlob_vote_phase` R:3593, manual open R:4874. Cancelled on all-voted R:5620 and R:5750. | lobby, finished. Gap: the Telegram early-submit path (R:5619-5628) leaves public_vote with no timer (D2). |
| mafia | night; day_discussion; day_vote; day_execution_confirm | `mafia_night_seconds` R:2775, `mafia_day_seconds` R:2790, `mafia_vote_seconds` R:2805 and R:2820. Floor 5 s. | night: start R:4706, after day vote with no candidate R:3391, after execution confirm R:3484. day_discussion: R:3274. day_vote: R:3307. day_execution_confirm: R:3354. Cancelled on manual or all-acted paths R:4960-4984, R:6250, R:6347, R:6436. | lobby, finished. |
| spy | none | n/a | n/a | lobby, freeplay, finished. |
| whoami | none | n/a | n/a | lobby, whoami_ask, whoami_answer, finished. |
| dice | none | n/a | n/a | lobby, freeplay, finished. |
| quiz | none | n/a | n/a | lobby, freeplay, finished. Auto-timers are deferred (GAME-FUTURE-B). |
| bredovukha | none | n/a | n/a | lobby, category_pick, private_answers, public_vote, finished. |
| bunker | none | n/a | n/a | lobby, bunker_reveal, bunker_vote, finished. |

Timer callbacks check kind, status, phase, round and `phase_started_at` before firing (R:2713-2723). The restore after restart covers mafia and zlobcards only (R:2846-2847).

---

## 4. Mini App (W)

- `_handle_game_action` W:6033-6135 checks visibility and the write lock, then delegates callback strings to `_execute_web_callback` W:3711. That covers the same prefixes as Telegram: start W:3893-3970, cancel W:3979-3992, reveal W:3995-4007, advance W:4009-4150, gmact W:4554-4576 (includes auto-advance), gmconfirm W:4612-4640 (includes auto-resolve).
- Form actions W:6136-6340: `spy_set_category` 6136, `whoami_set_category` 6151, `zlob_set_category` 6167, `spy_guess` 6183-6200, `whoami_ask` 6230-6247, `whoami_guess` 6248, `bred_submit` 6282, `zlob_submit` 6310.
- Mini App only: spy guess location. `GAME_STORE.spy_guess_location` (S:2354) has one caller, W:6185. Telegram has no handler. This matches GAME-FUTURE-A.
- The mafia night, execution-confirm, cancel and reveal paths were checked branch by branch. Other Mini App branches were matched by prefix, not line by line.

---

## 5. Divergences and observations

These are findings from the code read, not fixed in GUX-00. Each is a candidate follow-up.

- **D1. Mini App cancel and reveal skip confirmation and the expected-state guard.** W:3979-3992 and W:3995-4007 call `GAME_STORE.finish` with no expected status, phase or round. Telegram requires the `sok` or `rok` confirmation with expected state (R:5079-5107, R:4998-5077). Confirmed by reading.
- **D2. Zlobcards vote timer missing on the Telegram early-submit path.** When the last hand is submitted in Telegram, R:5619-5628 cancels the private timer and refreshes the board, but never calls `_schedule_phase_timer`. The Mini App path does schedule it (W:4342-4350). Result: in Telegram, public_vote has no timer until an admin presses "Закрыть раунд" (R:2081-2082). Confirmed by reading; not runtime-tested.
- **D3. No chat-id check on the bunker vote path.** `bunker_vote_callback` (R:5887-5992) and `bunker_register_vote` (S:3613-3641) take no chat argument, unlike other handlers. `bunker_register_reveal` (S:3508-3516) is similar. Low risk: the keyboards are DM-only (R:2637-2649, R:823-845).
- **D4. Mafia day-vote keyboard offers targets that the handler refuses.** The group board `gmvote` (R:581-598) shows all alive players including self, and the private keyboard excludes self and the protected target (R:601-626). `mafia_register_day_vote` refuses self (S:4052-4053) and the protected target (S:4054-4055). The group board can show a button that always fails.
- **O1. Lobby owner without manage can neither leave nor cancel.** Leave is refused (R:4531-4535), and cancel is manage-gated (R:4639-4650). Edge case: owners normally hold manage, because `/game` requires it (R:3883).
- **O2. Bredovukha allows voting for one's own lie** (S:2979-2980, no owner check). Scoring removes the own vote (S:3069-3070), so no points are gained. Zlobcards refuses own-card votes (S:3313-3314).
- **O3. Mixed-meaning phases.** `freeplay` means different things in spy, dice and quiz. `private_answers` and `public_vote` mean different things in zlobcards and bredovukha. Use the game kind, not the phase name alone.

---

## 6. Coverage check

- Phases per game: zlobcards (lobby, private_answers, public_vote, finished); spy (lobby, freeplay, finished); whoami (lobby, whoami_ask, whoami_answer, finished); mafia (lobby, night, day_discussion, day_vote, day_execution_confirm, finished); dice (lobby, freeplay, finished); quiz (lobby, freeplay, finished); bredovukha (lobby, category_pick, private_answers, public_vote, finished); bunker (lobby, bunker_reveal, bunker_vote, finished).
- Every row has a code citation. No row is a guess.
- The phase list matches the 14 values in `docs/GAME_UX_BASELINE.md` section 2 and S:29-44.
