# DB

This module stores all of the database-related code, including database model definition and database connection management (coming soon).


## Models

Models are defined in the `db/models` submodule, using SQLAlchemy's ORM. Each model corresponds to a table in the database, and the fields of the model correspond to columns in the table. For now we are only using SQLAlchemy's ORM for defining the models, we don't use it for database connection management or query execution.

## Database Migrations

We are using Alembic for database migrations. The migration files are stored in the `alembic/versions` directory. 

At startup both the api and the validator execute the `run_migrations()` method as part of the `initialize_database()` process, which ensures that the database schema is up to date with the latest migration files. If something goes wrong during the migration process, the application will fail to start and log the error.

### Managing migrations

As a contributor whenever you change a database table, add a new one or remove an existing one, you will need to create a new migration file. To create a new migration file, you can use the following command:

```bash
make migrations-generate DATABASE_USERNAME=alice DATABASE_PASSWORD=alice DATABASE_NAME=postgres DATABASE_HOST=localhost DATABASE_PORT=5432
```

Some details are not tracked automatically by alembic (e.g. functions, views,...), so you will need to edit the generated migration file and add manually the necessary SQL commands to create or modify those aspects of the database schema.

To apply the migrations to your local database, you can use the following command:

```bash
make migrations-upgrade DATABASE_USERNAME=alice DATABASE_PASSWORD=alice DATABASE_NAME=postgres DATABASE_HOST=localhost DATABASE_PORT=5432
```

To downgrade the database to a previous migration, you can use the following command:

```bash
make migrations-downgrade DATABASE_USERNAME=alice DATABASE_PASSWORD=alice DATABASE_NAME=postgres DATABASE_HOST=localhost DATABASE_PORT=5432
```

To check the current version of the database, you can use the following command:

```bash
make migrations-current DATABASE_USERNAME=alice DATABASE_PASSWORD=alice DATABASE_NAME=postgres DATABASE_HOST=localhost DATABASE_PORT=5432
```

To check the complete history of migrations, you can use the following command:

```bash
make migrations-history DATABASE_USERNAME=alice DATABASE_PASSWORD=alice DATABASE_NAME=postgres DATABASE_HOST=localhost DATABASE_PORT=5432
```

# TODO

[] Move the `utils/db.py` logic into this module to centralize the database-related code.