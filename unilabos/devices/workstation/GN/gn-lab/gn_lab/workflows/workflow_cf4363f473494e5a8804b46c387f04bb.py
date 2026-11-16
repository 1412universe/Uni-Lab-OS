from unilabos.workflow.authoring import device, workflow, workflow_output




@workflow(
    workflow_uuid="cf4363f4-7349-4e5a-8804-b46c387f04bb",
    displayname='probe data edge',
    tags=[],
    meta_data={},
    workflow_type='normal',
)
def probe_data_edge():
    """空工作流。"""
    return workflow_output()
