from unilabos.workflow.authoring import device, workflow, workflow_output




@workflow(
    workflow_uuid="bd6a2374-c9b1-427a-aa0a-27850c25f633",
    displayname='probe data edge',
    tags=[],
    meta_data={},
    workflow_type='normal',
)
def probe_data_edge():
    """空工作流。"""
    return workflow_output()
