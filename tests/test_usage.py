from agent_hover import daemon


def test_tree_usage_sums_children():
    table = {10: (1, 5.0, 2048), 11: (10, 20.0, 1024), 12: (11, 1.5, 1024), 20: (1, 99.0, 999999)}
    assert daemon.tree_usage(table, 10) == (26.5, 4)
    assert daemon.tree_usage(table, 404) == (0.0, 0)


def test_hot_only_when_not_working_for_long():
    hs = {}
    assert not daemon.hot_update(hs, "S", "done", 90, 0, 50, 2)
    assert not daemon.hot_update(hs, "S", "done", 90, 119, 50, 2)
    assert daemon.hot_update(hs, "S", "done", 90, 120, 50, 2)
    assert not daemon.hot_update(hs, "S", "working", 90, 130, 50, 2)  # working: busy is normal
    assert not daemon.hot_update(hs, "S", "idle", 90, 200, 50, 2)  # the clock starts again
    assert not daemon.hot_update(hs, "S", "idle", 10, 400, 50, 2)
    assert not daemon.hot_update(hs, "T", "idle", 90, 0, 0, 0)  # 0 %: off


def test_process_table_reads_ps():
    table = daemon.process_table()
    assert table and all(len(v) == 3 for v in table.values())
