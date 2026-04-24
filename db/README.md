# DB

This module stores all of the database-related code, including database model definition and database connection management (coming soon).


## Models

Models are defined in the `db/models` submodule, using SQLAlchemy's ORM. Each model corresponds to a table in the database, and the fields of the model correspond to columns in the table. For now we are only using SQLAlchemy's ORM for defining the models, we don't use it for database connection management or query execution.

## Database Migrations

We are using Alembic for database migrations. The migration files are stored in the `alembic/versions` directory. 

At startup both the api and the validator execute the `run_migrations()` method as part of the `initialize_database()` process, which ensures that the database schema is up to date with the latest migration files. If something goes wrong during the migration process, the application will fail to start and log the error.

### Managing migrations

- Create new migration
- Add manually functions and other aspects that are not automatically detected by alembic

# TODO

[] Move the `utils/db.py` logic into this module to centralize the database-related code.