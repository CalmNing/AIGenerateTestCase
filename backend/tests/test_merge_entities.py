from app.pipeline.pipeline_runner import merge_entities_by_position


def test_merge_keeps_existing_when_fewer_generated():
    existing = [object(), object(), object()]
    generated = [{"a": 1}]  # 只生成 1 条
    to_update, to_create, to_delete = merge_entities_by_position(existing, generated)
    assert len(to_update) == 1
    assert to_create == []
    assert len(to_delete) == 2  # 多余 2 条删除


def test_merge_creates_when_more_generated():
    existing = [object()]
    generated = [{"a": 1}, {"b": 2}]
    to_update, to_create, to_delete = merge_entities_by_position(existing, generated)
    assert len(to_update) == 1
    assert len(to_create) == 1
    assert to_delete == []


def test_merge_skips_empty_placeholder():
    existing = [object(), object()]
    generated = [{}, {"b": 2}]  # 第 0 条为空占位
    to_update, to_create, to_delete = merge_entities_by_position(existing, generated)
    assert len(to_update) == 1  # 只更新第 1 条
    assert to_create == []
    assert to_delete == []      # 第 0 条已有实体不被删
