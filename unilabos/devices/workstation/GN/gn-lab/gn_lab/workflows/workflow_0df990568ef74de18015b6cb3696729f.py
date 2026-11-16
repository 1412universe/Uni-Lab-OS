from unilabos.workflow.authoring import device, workflow, workflow_output




@workflow(
    workflow_uuid="0df99056-8ef7-4de1-8015-b6cb3696729f",
    displayname='probe data edge',
    tags=[],
    meta_data={},
    workflow_type='normal',
)
def probe_data_edge():
    """空工作流。"""
    return workflow_output()
