"""Pipeline 生成后处理：一致性 + 置信度计算。"""
from db.models import ApiEndpoint, ApiProject

from app.pipeline.pipeline_runner import _compute_accuracy


def test_compute_accuracy_writes_report(db_session):
    project = ApiProject(name="p")
    db_session.add(project)
    db_session.commit()
    ep = ApiEndpoint(project_id=project.id, name="c", method="POST", path="/x",
                     request_schema={"type": "object", "required": ["name"],
                                     "properties": {"name": {"type": "string"}}})
    db_session.add(ep)
    db_session.commit()
    case = {"steps": [{"endpoint_ref": 1, "body": '{"name": "ok"}'}], "preconditions": ["已登录"]}
    index_to_id = {1: ep.id}
    report, score, level = _compute_accuracy(case, index_to_id, db_session)
    assert report["passed"] is True
    assert score >= 0.8
    assert level == "high"
