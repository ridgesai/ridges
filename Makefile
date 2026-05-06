# ---------------------------------------------------------------------------
# Database credentials — pass on the command line or set in your environment:
#   make migrations-upgrade DATABASE_USERNAME=alice DATABASE_PASSWORD=secret DATABASE_NAME=ridges
# HOST defaults to localhost, PORT defaults to 5432.
# ---------------------------------------------------------------------------

guard-%:
	@if [ -z '${$*}' ]; then echo 'ERROR: $* is required' >&2; exit 1; fi

_DB_GUARDS = guard-DATABASE_USERNAME guard-DATABASE_PASSWORD guard-DATABASE_NAME
_DB_ENV    = DATABASE_USERNAME=$(DATABASE_USERNAME) \
             DATABASE_PASSWORD=$(DATABASE_PASSWORD) \
             DATABASE_HOST=$(or $(DATABASE_HOST),localhost) \
             DATABASE_PORT=$(or $(DATABASE_PORT),5432) \
             DATABASE_NAME=$(DATABASE_NAME)

.PHONY: migrations-generate migrations-upgrade migrations-downgrade migrations-history migrations-current

## Generate a new migration from model changes.
## Usage: make migrations-generate MESSAGE="add foo column"
migrations-generate: $(_DB_GUARDS)
	$(_DB_ENV) uv run alembic revision --autogenerate -m "$(MESSAGE)"

## Upgrade to head (default) or a specific revision.
## Usage: make migrations-upgrade
##        make migrations-upgrade REVISION=abc123
migrations-upgrade: $(_DB_GUARDS)
	$(_DB_ENV) uv run alembic upgrade $(or $(REVISION),head)

## Downgrade one step (default) or to a specific revision.
## Usage: make migrations-downgrade
##        make migrations-downgrade REVISION=-2
migrations-downgrade: $(_DB_GUARDS)
	$(_DB_ENV) uv run alembic downgrade $(or $(REVISION),-1)

## Show the full migration history.
migrations-history: $(_DB_GUARDS)
	$(_DB_ENV) uv run alembic history --verbose

## Show the current applied revision.
migrations-current: $(_DB_GUARDS)
	$(_DB_ENV) uv run alembic current
