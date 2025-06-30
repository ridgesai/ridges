# SQLAlchemy Migration Guide

This document outlines the partial migration of the database operations from raw SQL queries to SQLAlchemy ORM.

## Overview

The `api/src/db/operations.py` module has been partially refactored to use SQLAlchemy for better maintainability, type safety, and developer experience. The migration maintains full backwards compatibility while introducing modern ORM patterns.

## What Was Refactored

### Simple CRUD Operations (Now using SQLAlchemy)

✅ **Completed Migrations:**
- `store_agent()` - Agent storage with conflict resolution
- `store_agent_version()` - Agent version storage with conflict resolution
- `get_agent_by_hotkey()` - Agent retrieval by miner hotkey
- `get_agent()` - Agent retrieval by ID
- `get_agent_version()` - Agent version retrieval
- `get_latest_agent_version()` - Latest version retrieval
- `get_num_agents()` - Agent count
- `store_weights()` - Weights storage
- `get_latest_weights()` - Latest weights retrieval
- `get_current_top_miner()` - Top miner identification

### Complex Queries (Still using raw SQL)

❌ **Remaining Raw SQL (Complex Queries):**
- `get_top_agents()` - Complex ranking with DISTINCT ON and CASE
- `get_recent_executions()` - Multi-table joins with nested loops
- `get_latest_execution_by_agent()` - Complex priority-based selection
- `get_agent_summary()` - Multi-step aggregations
- `get_evaluations()` - Complex filtering and ordering
- `get_top_miner_fraction_last_24h()` - Time-window analysis with CTEs
- `get_weights_history_last_24h_with_prior()` - Complex UNION queries
- `get_queue_info()` - Queue position calculations

## Architecture

### New Components

1. **`sqlalchemy_models.py`** - SQLAlchemy ORM models
   - `AgentModel` - Maps to `agents` table
   - `AgentVersionModel` - Maps to `agent_versions` table
   - `EvaluationModel` - Maps to `evaluations` table
   - `EvaluationRunModel` - Maps to `evaluation_runs` table
   - `WeightsHistoryModel` - Maps to `weights_history` table

2. **`sqlalchemy_manager.py`** - SQLAlchemy operations manager
   - Connection pooling with SQLAlchemy engine
   - ORM-based CRUD operations
   - Session management

3. **Enhanced `operations.py`** - Hybrid approach
   - Uses SQLAlchemy for simple operations
   - Falls back to raw SQL if SQLAlchemy fails
   - Maintains existing interface

### Fallback Strategy

Each refactored method follows this pattern:

```python
def store_agent(self, agent: Agent) -> int:
    try:
        # Try SQLAlchemy first
        return self.sqlalchemy_manager.store_agent(agent)
    except Exception as e:
        logger.error(f"SQLAlchemy failed, falling back to raw SQL: {e}")
        # Fallback to original raw SQL implementation
        # ... original code ...
```

## Benefits

### ✅ Immediate Benefits
- **Type Safety**: SQLAlchemy models provide compile-time type checking
- **Maintainability**: ORM abstracts away SQL syntax details
- **Relationship Management**: Automatic handling of foreign key relationships
- **Connection Pooling**: Modern connection pool management
- **Query Building**: Programmatic query construction
- **Backwards Compatibility**: Zero breaking changes to existing code

### 🔄 Future Benefits (when fully migrated)
- **Database Migrations**: Alembic integration for schema changes
- **Query Optimization**: SQLAlchemy query analysis and optimization
- **Multiple Database Support**: Easy switching between PostgreSQL, MySQL, etc.
- **Testing**: Easier unit testing with in-memory databases
- **Documentation**: Auto-generated schema documentation

## Usage Examples

### Simple Operations (Now using SQLAlchemy)

```python
from api.src.db.operations import DatabaseManager
from api.src.utils.models import Agent, AgentVersion

db = DatabaseManager()

# Store an agent (uses SQLAlchemy)
agent = Agent(...)
result = db.store_agent(agent)

# Get an agent (uses SQLAlchemy)
agent = db.get_agent_by_hotkey("some_hotkey")

# Get agent count (uses SQLAlchemy)
count = db.get_num_agents()
```

### Complex Operations (Still using raw SQL)

```python
# Complex ranking query (still raw SQL)
top_agents = db.get_top_agents(limit=10)

# Complex execution analysis (still raw SQL)
recent_executions = db.get_recent_executions(count=50)
```

## Performance Considerations

### SQLAlchemy Operations
- **Connection Pooling**: Modern pool with 10 base connections, 20 overflow
- **Session Management**: Automatic session cleanup
- **Query Efficiency**: ORM generates optimized SQL

### Raw SQL Operations
- **Legacy Pool**: Existing psycopg2 ThreadedConnectionPool
- **Direct Execution**: No ORM overhead for complex queries

## Migration Roadmap

### Phase 1: ✅ Completed
- [x] Set up SQLAlchemy models and manager
- [x] Migrate simple CRUD operations
- [x] Implement fallback strategy
- [x] Add comprehensive documentation

### Phase 2: 🔄 Future Work
- [ ] Migrate complex queries using SQLAlchemy Core
- [ ] Add Alembic for database migrations
- [ ] Implement comprehensive test suite
- [ ] Performance optimization and monitoring

### Phase 3: 🔮 Long-term
- [ ] Complete migration to SQLAlchemy
- [ ] Remove raw SQL fallbacks
- [ ] Add advanced ORM features (lazy loading, etc.)
- [ ] Database abstraction layer

## Testing

### Current Testing Strategy
- Each SQLAlchemy method includes fallback to proven raw SQL
- Existing functionality is preserved
- No changes to external interfaces

### Future Testing Improvements
- Unit tests for SQLAlchemy models
- Integration tests for complex queries
- Performance benchmarking
- Database migration testing

## Contributing

When adding new database operations:

1. **Simple Operations**: Use SQLAlchemy ORM with fallback
2. **Complex Operations**: Start with raw SQL, plan SQLAlchemy migration
3. **Always**: Maintain backwards compatibility
4. **Testing**: Ensure both SQLAlchemy and fallback paths work

## Configuration

### Environment Variables
All existing database configuration remains the same:
- `AWS_RDS_PLATFORM_ENDPOINT`
- `AWS_MASTER_USERNAME`
- `AWS_MASTER_PASSWORD`
- `AWS_RDS_PLATFORM_DB_NAME`

### Connection Pools
- **SQLAlchemy**: Modern pool (10 base, 20 overflow, 1hr recycle)
- **psycopg2**: Legacy pool (5-50 connections) for complex queries

## Troubleshooting

### Common Issues

1. **Import Errors**: Ensure SQLAlchemy is installed (`pip install sqlalchemy>=2.0.41`)
2. **Connection Issues**: Check that both pools can connect
3. **Model Mismatches**: Verify SQLAlchemy models match database schema
4. **Performance**: Monitor query performance in logs

### Debugging

- SQLAlchemy operations are logged with fallback information
- Set `echo=True` in SQLAlchemy engine for query debugging
- Both successful operations and fallbacks are logged

### Rollback Strategy

If issues arise:
1. SQLAlchemy methods automatically fallback to raw SQL
2. No manual intervention required
3. All original functionality preserved
4. Disable SQLAlchemy by commenting out `self.sqlalchemy_manager` initialization

This migration provides a solid foundation for modernizing the database layer while maintaining reliability and performance.