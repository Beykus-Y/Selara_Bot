# Зафиксированные Python dependencies

Корневой `uv.lock` — единый universal lock app и workspace member `gacha`.
Он фиксирует версии и hashes прямых/транзитивных dependencies, dev extras,
build tools и audit/browser tooling. CI и Docker используют Python 3.12
и uv 0.11.33; для разных Python/platform marker ветвей lock может содержать
разные версии одного package. Для одинаковой платформы/версии Python один
commit определяет один выбранный graph. Registry — public PyPI.

## Установка

CI проверяет `uv lock --check`, затем устанавливает весь workspace с dev extras
и группами `build`, `audit`. Production image выбирает только свой package
(`selara` / `selara-gacha`), без dev extras и audit/browser group. Frontend CI
использует только группу `browser`: Playwright/Jinja2 из того же lock, без
отдельного плавающего `pip install`.

Установка идёт в два шага: сначала locked wheels и build tools без проектов,
затем сборка наших packages с `--no-build-isolation` на этих tools. У первого
шага `--no-build`: зависимости без подходящего wheel приводят к ошибке,
а не запускают стороннюю сборку с незаписанными build dependencies.
Все команды используют `--locked`, а не `--frozen`: устаревший lock не
разрешается заново в CI/image build. uv binary в Docker закреплён registry
digest, в CI — точной версией. Runtime images используют `.venv/bin` в PATH;
миграции, assets и cwd gacha сохраняют прежние пути `/app`.

CI проверяет installed distributions против lock и строит оба production
images; внутри каждого проверяет его установленный graph. Реальные PostgreSQL
и gacha backup/container smoke checks продолжают выполняться.
`pip-audit` проверяет установленный locked CI graph, включая production deps.

## Обновление

Меняйте manifest, затем создавайте новый lock в отдельной ветке от `dev`:

```bash
uv lock
# При обновлении конкретного package:
uv lock --upgrade-package <name>
# Осознанное обновление всего graph:
uv lock --upgrade
```

Коммитьте `pyproject.toml`, `gacha/pyproject.toml` (если менялся) и `uv.lock`
вместе. Не редактируйте версии/hashes lock вручную и не удаляйте его для
обычного запуска. PR должен показать diff зависимостей и пройти backend/frontend.
Dependabot `package-ecosystem: uv` проверяет workspace раз в неделю и создаёт
PR в `dev`; обновления не сливаются автоматически. GitHub читает конфигурацию
Dependabot из default branch: после обычного promotion в `main` она начнёт
действовать. Настройки repository этим PR не меняются.

Для обновления uv также согласуйте версию в CI, digest обоих Dockerfiles и
повторно создайте lock этим resolver. Toolchain обновляется отдельным diff.

Lock фиксирует Python graph, а не все bytes Docker image: Python/base image,
Ubuntu/Debian apt packages и Chromium/system libraries имеют собственные
жизненные циклы. Для точного повторного deploy используйте сохранённые image
digests из [IMMUTABLE_DEPLOY.md](IMMUTABLE_DEPLOY.md).

Документация: [uv workspaces](https://docs.astral.sh/uv/concepts/workspaces/),
[uv Docker integration](https://docs.astral.sh/uv/guides/integration/docker/),
[Dependabot ecosystems](https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories).
