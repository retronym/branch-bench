from branch_bench.runner import bisect_order


def test_bisect_order_empty():
    assert bisect_order(0) == []


def test_bisect_order_one():
    assert bisect_order(1) == [0]


def test_bisect_order_two():
    assert bisect_order(2) == [0, 1]


def test_bisect_order_five():
    order = bisect_order(5)
    # First two must be endpoints
    assert order[0] == 0
    assert order[1] == 4
    # Midpoint next
    assert order[2] == 2
    # All indices present exactly once
    assert sorted(order) == list(range(5))


def test_bisect_order_coverage():
    for n in range(1, 20):
        order = bisect_order(n)
        assert sorted(order) == list(range(n)), f"n={n}: missing indices"
        assert len(set(order)) == n, f"n={n}: duplicates"


def test_bisect_order_endpoints_first():
    for n in [3, 5, 8, 16]:
        order = bisect_order(n)
        assert order[0] == 0
        assert order[1] == n - 1
