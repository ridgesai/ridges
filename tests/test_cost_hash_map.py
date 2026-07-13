from uuid import uuid4

from inference_gateway.cost_hash_map import CostHashMap


def test_attempts_have_independent_budgets():
    cost_map = CostHashMap()
    run_id = uuid4()

    cost_map.add_cost((run_id, 1), 1.5)
    assert cost_map.get_cost((run_id, 1)) == 1.5
    # A new attempt starts from a zero budget.
    assert cost_map.get_cost((run_id, 2)) == 0

    cost_map.add_cost((run_id, 2), 0.25)
    assert cost_map.get_cost((run_id, 2)) == 0.25
    assert cost_map.get_cost((run_id, 1)) == 1.5
