from unilabos.workflow.authoring import device, workflow, workflow_output




@workflow(
    workflow_uuid="f3f3d384-9c52-4177-beb8-866e3c0905c8",
    displayname='probe-live',
    tags=[],
    meta_data={},
    workflow_type='normal',
)
def probe_live():
    """空工作流。"""
    return workflow_output()
