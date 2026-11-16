from gn_lab.devices.gn_prcxi import GN_PRCXI9300Handler
from gn_lab.devices.gn_station_devices import CentrifugeDevice
from gn_lab.devices.gn_station_devices import RoboticArmDevice
from unilabos.workflow.authoring import device, workflow, workflow_output


gn_prcxi9300handler: GN_PRCXI9300Handler = device('PRCXI')
centrifugedevice: CentrifugeDevice = device('gn_centrifuge')
roboticarmdevice: RoboticArmDevice = device('gn_robotic_arm')


@workflow(
    workflow_uuid="5d2b64d1-2666-4459-a587-708aadf0a5b3",
    displayname='金四面体-从第一次离心到结束',
    description='由 金四面体-从第一次离心到结束.json 转换，节点身份保持不变。',
)
def gold_tetrahedron_after_first_centrifuge():
    # unilab:node_uuid=975b3b0f-ed10-4ed5-81e7-b4ad976cfa2d
    step_001 = roboticarmdevice.pick(item_type='plate', number=1, station='nine9320', timeout=30000, x_speed=300)
    # unilab:node_uuid=ed96fc22-692a-4d3a-bf62-6e2e82186a53
    step_002 = roboticarmdevice.place(item_type='plate', number=1, station='centrifuge', timeout=30000, x_speed=300)
    # unilab:node_uuid=0d34423d-f74c-4eba-a4fe-b9710f716f83
    step_003 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=58e951e1-6e33-4b06-82fd-f6d08083167d
    step_004 = centrifugedevice.execute_command(cmd_type=5, inner_z_pos=3590, plate_no=1, rpm=0, time_minutes=1, timeout=180, y_pos=-2160, y_speed=300, z_pos=1050, z_speed=300)
    # unilab:node_uuid=0a99400b-5d9a-45fa-8268-fff3194044bf
    step_005 = roboticarmdevice.pick(item_type='plate', number=5, station='stack', timeout=30000, x_speed=300)
    # unilab:node_uuid=015da55f-1a6f-44b1-b4ce-3092cad342b9
    step_006 = roboticarmdevice.place(item_type='plate', number=1, station='nine9320', timeout=30000, x_speed=300)
    # unilab:node_uuid=b9cd11b6-dbd2-4215-91e6-c05f5b1ae558
    step_007 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=2d2ca515-6ea4-4437-970c-25aff698e090
    step_008 = gn_prcxi9300handler.titration_liquid(asp_flow_rates=[300], asp_vols=[700], blow_out_air_volume=[20], blow_out_air_volume_before=[], cycle_delay_s=10, dis_flow_rates=[300], dis_vols=[700], dispensing_method=['DiveToBottom'], hover_below_liquid_level=None, is_96_well=True, liquid_height=None, none_keys=[], offsets=None, post_air_volume=None, post_discharge_pause_time_ms=None, repeat_count=2, sources=[{'uuid': 'bd6f7d88-e96a-4efa-b8c6-f5514aeb7cdf'}], targets=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], tip_racks=[{'uuid': '695548f2-3e97-4913-a2c5-e19b3b07cac9'}], use_channels=[], z_start_point_offset_height=None)
    # unilab:node_uuid=4d26f0d3-5e2f-4de8-8041-4857ffb17459
    step_009 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=7b2f6cfc-f5c3-469b-bf0a-d5667f1971cd
    step_010 = roboticarmdevice.pick(item_type='plate', number=1, station='nine9320', timeout=30000, x_speed=300)
    # unilab:node_uuid=43998fb7-155d-49f8-8550-f6ddee193fd2
    step_011 = roboticarmdevice.place(item_type='plate', number=1, station='centrifuge', timeout=30000, x_speed=300)
    # unilab:node_uuid=eb5c0ea2-b75e-48aa-a52b-1a0c7ef3109d
    step_012 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=e28e9baf-a6f3-400c-89c5-95f0ffa52630
    step_013 = centrifugedevice.execute_command(cmd_type=5, inner_z_pos=3590, plate_no=3, rpm=0, time_minutes=1, timeout=180, y_pos=-2160, y_speed=300, z_pos=1050, z_speed=300)
    # unilab:node_uuid=0fff6034-e3fd-4024-bca0-6b3cac5ddeb4
    step_014 = centrifugedevice.execute_command(cmd_type=6, plate_no=1, rpm=4000, temperature=20, time_minutes=30, timeout=2500)
    # unilab:node_uuid=527f67e0-0c84-4486-9a7e-8f3b8bdb086c
    step_015 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=563df143-94cf-4104-b852-ba3595707920
    step_016 = centrifugedevice.execute_command(cmd_type=7, inner_z_pos=3590, plate_no=1, rpm=0, time_minutes=0, timeout=120, y_pos=-2160, y_speed=300, z_pos=1050, z_speed=300)
    # unilab:node_uuid=c55e03da-fa4d-4721-a4f5-9d7594fc9d77
    step_017 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=a4b7b3ed-7240-4ea2-a1e5-e287384c1916
    step_018 = roboticarmdevice.pick(item_type='plate', number=1, station='centrifuge', timeout=30000, x_speed=300)
    # unilab:node_uuid=d26e79ff-eb7d-41b4-b33e-dc497547813a
    step_019 = roboticarmdevice.place(item_type='plate', number=1, station='nine9320', timeout=30000, x_speed=300)
    # unilab:node_uuid=e0f2b801-381c-47a4-bd71-2b1af5982597
    step_020 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=aa36ae20-22cc-4675-b31d-84219fdabf3f
    step_021 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, force=5, from_=16, get_direction='0', hierarchy=1, intermediate_locations=[], pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=1, plate=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], put_down_position=1, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=10)
    # unilab:node_uuid=508e0153-77aa-4e20-a281-6f20365300af
    step_022 = gn_prcxi9300handler.titration_liquid(asp_flow_rates=[300], asp_vols=[700], blow_out_air_volume=[20], blow_out_air_volume_before=[], cycle_delay_s=10, dis_flow_rates=[300], dis_vols=[700], dispensing_method=['DiveToBottom'], hover_below_liquid_level=None, is_96_well=True, liquid_height=None, none_keys=[], offsets=None, post_air_volume=None, post_discharge_pause_time_ms=None, repeat_count=2, sources=[{'uuid': '8e1cb526-32c4-4c6c-bb22-6faa81e209c5'}], targets=[{'uuid': '53aca277-6dea-43a5-89d2-83b15a4a01b7'}], tip_racks=[{'uuid': '0014ea7c-9743-452a-96ab-5407f475a32c'}], use_channels=[], z_start_point_offset_height=None)
    # unilab:node_uuid=aa07bbf7-d401-4a9b-8859-d40e03d57172
    step_023 = gn_prcxi9300handler.transfer_liquid(asp_flow_rates=[300], asp_vols=[500], blow_out_air_volume=[20], blow_out_air_volume_before=None, delays=None, dis_flow_rates=[300], dis_vols=[500], dispensing_method=[], hover_below_liquid_level=[], is_96_well=True, liquid_height=None, mix_liquid_height=None, mix_rate=None, mix_stage='none', mix_times=None, mix_vol=None, none_keys=[], offsets=None, post_air_volume=[], post_discharge_pause_time_ms=[], source_slots=8, sources=[{'uuid': '39051c8a-0180-4516-88e1-fa7ec724f453'}], spread='wide', target_slots=16, targets=[{'uuid': '8e1cb526-32c4-4c6c-bb22-6faa81e209c5'}], tip_rack_slots=11, tip_racks=[{'uuid': '2c525d62-d238-48d5-892a-993e5c0065d7'}], touch_tip=False, use_channels=[], z_start_point_offset_height=[])
    # unilab:node_uuid=8e67451a-1047-41d7-9e99-d172359c11b8
    step_024 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, force=5, from_=10, get_direction='0', hierarchy=1, intermediate_locations=[], pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=1, plate=[{'uuid': '8e1cb526-32c4-4c6c-bb22-6faa81e209c5'}], put_down_position=1, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=16)
    # unilab:node_uuid=57a1d9b9-ee84-4056-89dd-a4b0e9e39594
    step_025 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=3e0bdd32-7f16-474e-916d-ccdd647cd19a
    step_026 = roboticarmdevice.pick(item_type='plate', number=1, station='nine9320', timeout=30000, x_speed=300)
    # unilab:node_uuid=d58573f3-d873-40a0-889b-d7bbd9af81b4
    step_027 = roboticarmdevice.place(item_type='plate', number=1, station='centrifuge', timeout=30000, x_speed=300)
    # unilab:node_uuid=ff806475-3a08-4348-8500-4460738e93c5
    step_028 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=9a3a1776-6daf-45cb-b045-bd30561f1241
    step_029 = centrifugedevice.execute_command(cmd_type=5, inner_z_pos=3600, plate_no=1, rpm=0, time_minutes=1, timeout=180, y_pos=-2160, y_speed=300, z_pos=1050, z_speed=300)
    # unilab:node_uuid=232b1ef4-7b05-4029-a735-b5bf56e238b7
    step_030 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=98bf3ca0-ccdf-491b-aa6c-79ab651ab69d
    step_031 = centrifugedevice.execute_command(cmd_type=7, inner_z_pos=3590, plate_no=3, rpm=0, time_minutes=0, timeout=120, y_pos=-2160, y_speed=300, z_pos=1050, z_speed=300)
    # unilab:node_uuid=d22ee331-b776-4f31-ab3a-a2f8d7cfeaec
    step_032 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=5960c2d7-d518-4b26-88a9-a307937a1933
    step_033 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=bd1bb5ec-8716-42ae-ab48-02bcad84960a
    step_034 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, drop_direction='', force=5, from_=15, get_direction='', hierarchy=1, intermediate_locations=[], pickup_direction='', pickup_distance_from_top=0, pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=2, plate=[{'uuid': '695548f2-3e97-4913-a2c5-e19b3b07cac9'}], put_direction='', put_down_position=2, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=16)
    # unilab:node_uuid=f2833017-eeba-408f-9b66-57586d17c451
    step_035 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=f282cb75-62e9-4e25-a39d-af19bff38b70
    step_036 = roboticarmdevice.pick(item_type='plate', number=1, station='nine9320', timeout=180, x_speed=300)
    # unilab:node_uuid=1790cc18-8c8f-4b15-89ad-b33a3b780439
    step_037 = roboticarmdevice.place(item_type='plate', number=66, station='stack', timeout=180, x_speed=300)
    # unilab:node_uuid=77a65421-d91d-480f-afe2-ef8ce5d064a0
    step_038 = roboticarmdevice.pick(item_type='plate', number=69, station='stack', timeout=180, x_speed=300)
    # unilab:node_uuid=eafdd965-01cb-430a-872a-c35defba7b28
    step_039 = roboticarmdevice.place(item_type='plate', number=1, station='nine9320', timeout=180, x_speed=300)
    # unilab:node_uuid=ed2c859a-d61d-492d-b622-610aa8c29d79
    step_040 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=9bec5047-a489-420a-a55a-7b7005720491
    step_041 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, drop_direction='', force=5, from_=16, get_direction='', hierarchy=1, intermediate_locations=[], pickup_direction='', pickup_distance_from_top=0, pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=2, plate=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], put_direction='', put_down_position=2, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=15)
    # unilab:node_uuid=8097a586-ac63-4910-b7aa-e8c59e7487be
    step_042 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=9d48a26c-cd9f-4a59-a429-753bc911017e
    step_043 = roboticarmdevice.pick(item_type='plate', number=1, station='centrifuge', timeout=30000, x_speed=300)
    # unilab:node_uuid=bf1aeee3-4c5b-4492-bf76-ffeddbe259c6
    step_044 = roboticarmdevice.place(item_type='plate', number=1, station='nine9320', timeout=30000, x_speed=300)
    # unilab:node_uuid=f8e71a09-60f1-4493-b91d-7b3ceba38207
    step_045 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=ecad80e3-a927-4c49-b0f4-56bf25b61d71
    step_046 = gn_prcxi9300handler.transfer_liquid(asp_flow_rates=[300], asp_vols=[900], blow_out_air_volume=[20], blow_out_air_volume_before=None, delays=None, dis_flow_rates=[300], dis_vols=[900], dispensing_method=[], hover_below_liquid_level=[], is_96_well=True, liquid_height=None, mix_liquid_height=None, mix_rate=None, mix_stage='none', mix_times=None, mix_vol=None, none_keys=[], offsets=None, post_air_volume=[], post_discharge_pause_time_ms=[], source_slots=7, sources=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], spread='wide', target_slots=16, targets=[{'uuid': 'bd6f7d88-e96a-4efa-b8c6-f5514aeb7cdf'}], tip_rack_slots=15, tip_racks=[{'uuid': '695548f2-3e97-4913-a2c5-e19b3b07cac9'}], touch_tip=False, use_channels=[], z_start_point_offset_height=[])
    # unilab:node_uuid=13761185-6935-493f-a381-594f91683d03
    step_047 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=befa476f-82b5-4f6a-8849-131f47e391ef
    step_048 = roboticarmdevice.pick(item_type='plate', number=1, station='nine9320', timeout=30000, x_speed=300)
    # unilab:node_uuid=1492d73e-cd7b-456b-9d77-4f0cfd2f520f
    step_049 = roboticarmdevice.place(item_type='plate', number=1, station='centrifuge', timeout=30000, x_speed=300)
    # unilab:node_uuid=f638d11d-02b7-4da2-ac9b-ff6c268804b8
    step_050 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=5e20fb58-811e-424b-9c05-a22a04fa8e95
    step_051 = centrifugedevice.execute_command(cmd_type=5, inner_z_pos=3600, plate_no=3, rpm=0, time_minutes=1, timeout=180, y_pos=-2160, y_speed=300, z_pos=1050, z_speed=300)
    # unilab:node_uuid=efae9896-2a13-4260-a069-5abffd0065e3
    step_052 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=d1beaf06-9259-4c92-afbb-7f605897419a
    step_053 = centrifugedevice.execute_command(cmd_type=6, plate_no=1, rpm=4000, temperature=20, time_minutes=30, timeout=2500)
    # unilab:node_uuid=054c0195-1f6c-49ac-bf5f-03927167f505
    step_054 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=0041e7ee-ccce-469c-beed-6793056b237c
    step_055 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=22d49afc-d845-48db-afc3-cf987eb2c8d3
    step_056 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, drop_direction='', force=5, from_=15, get_direction='', hierarchy=1, intermediate_locations=[], pickup_direction='', pickup_distance_from_top=0, pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=2, plate=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], put_direction='', put_down_position=2, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=16)
    # unilab:node_uuid=a408b456-394d-4b50-9e7f-253902a30f18
    step_057 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=fa94b09e-27f8-4e58-a0dc-f5bdf5b6bfec
    step_058 = roboticarmdevice.pick(item_type='plate', number=1, station='nine9320', timeout=180, x_speed=300)
    # unilab:node_uuid=3c3cea39-24bc-4a5c-b379-e1ca39e4e1dd
    step_059 = roboticarmdevice.place(item_type='plate', number=67, station='stack', timeout=180, x_speed=300)
    # unilab:node_uuid=0bde958f-e624-4012-a5f0-77c712111423
    step_060 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=d8dd8bc3-0037-4773-baa7-8e499c128ece
    step_061 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, drop_direction='', force=5, from_=12, get_direction='', hierarchy=1, intermediate_locations=[], pickup_direction='', pickup_distance_from_top=0, pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=2, plate=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], put_direction='', put_down_position=2, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=16)
    # unilab:node_uuid=510db3c6-1b27-42c4-a178-4d5531c7b8c5
    step_062 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=272fa7ff-2b38-488f-a18e-971b62f022a1
    step_063 = roboticarmdevice.pick(item_type='plate', number=1, station='nine9320', timeout=180, x_speed=300)
    # unilab:node_uuid=b2f2f84e-b296-46b4-8c86-939c675ff8e0
    step_064 = roboticarmdevice.place(item_type='plate', number=68, station='stack', timeout=180, x_speed=300)
    # unilab:node_uuid=4e1e6879-5544-4796-85ac-704e59d5448d
    step_065 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=d7b04dfe-f1a2-4a0a-8c07-ddd5ca5e5b61
    step_066 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, drop_direction='', force=5, from_=11, get_direction='', hierarchy=1, intermediate_locations=[], pickup_direction='', pickup_distance_from_top=0, pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=2, plate=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], put_direction='', put_down_position=2, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=16)
    # unilab:node_uuid=d353149f-24ba-4393-a555-008f1e878c0d
    step_067 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=e0f6fdb3-82d8-4229-8389-9547333b8aa1
    step_068 = roboticarmdevice.pick(item_type='plate', number=1, station='nine9320', timeout=180, x_speed=300)
    # unilab:node_uuid=fa7915b5-cc62-457e-81f2-cc263d65bca7
    step_069 = roboticarmdevice.place(item_type='plate', number=69, station='stack', timeout=180, x_speed=300)
    # unilab:node_uuid=4e9212d3-f011-46d5-9d87-f172ab23257e
    step_070 = roboticarmdevice.pick(item_type='plate', number=70, station='stack', timeout=180, x_speed=300)
    # unilab:node_uuid=bff880a7-1659-42b5-a78b-a31d2502ec28
    step_071 = roboticarmdevice.place(item_type='plate', number=1, station='nine9320', timeout=180, x_speed=300)
    # unilab:node_uuid=03241952-316e-47a8-9d8c-a64b96d6f0f7
    step_072 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=05632439-4d43-4261-b729-9a06df8f9f08
    step_073 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, drop_direction='', force=5, from_=16, get_direction='', hierarchy=1, intermediate_locations=[], pickup_direction='', pickup_distance_from_top=0, pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=2, plate=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], put_direction='', put_down_position=2, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=11)
    # unilab:node_uuid=54e28e67-fa6d-431f-a392-b747d1458d98
    step_074 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=2bd18774-88c7-488c-adb8-9aff7dff663e
    step_075 = roboticarmdevice.pick(item_type='plate', number=71, station='stack', timeout=180, x_speed=300)
    # unilab:node_uuid=2b71744a-082b-444f-b0b6-c2aac0a11d2a
    step_076 = roboticarmdevice.place(item_type='plate', number=1, station='nine9320', timeout=180, x_speed=300)
    # unilab:node_uuid=9f26f5c7-a95b-45d2-b7fa-63c3be2b9253
    step_077 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=11c53244-9289-4d9e-a3e9-81b1664e2163
    step_078 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, drop_direction='', force=5, from_=16, get_direction='', hierarchy=1, intermediate_locations=[], pickup_direction='', pickup_distance_from_top=0, pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=2, plate=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], put_direction='', put_down_position=2, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=12)
    # unilab:node_uuid=0bbe8d87-3cfc-44dd-b2ff-daffd62dae90
    step_079 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=ee1043ca-1a2e-44a8-a93d-b74779b7c00c
    step_080 = roboticarmdevice.pick(item_type='plate', number=72, station='stack', timeout=180, x_speed=300)
    # unilab:node_uuid=9bbfe494-f184-41d1-99cf-3bb607a601aa
    step_081 = roboticarmdevice.place(item_type='plate', number=1, station='nine9320', timeout=180, x_speed=300)
    # unilab:node_uuid=82417c19-69f1-4bac-8373-b52ecdaab33e
    step_082 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=a42412f0-677d-4d8f-819b-8f82418076be
    step_083 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, drop_direction='', force=5, from_=16, get_direction='', hierarchy=1, intermediate_locations=[], pickup_direction='', pickup_distance_from_top=0, pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=2, plate=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], put_direction='', put_down_position=2, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=15)
    # unilab:node_uuid=fdabeae0-d1ae-4967-9ccc-abdcdf7324c7
    step_084 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=3e64a662-132d-470a-ac0f-44497a2096eb
    step_085 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=abd1d6bf-8a11-4c04-80e5-c09ab543f89d
    step_086 = centrifugedevice.execute_command(cmd_type=7, inner_z_pos=3590, plate_no=1, rpm=0, time_minutes=0, timeout=120, y_pos=-2160, y_speed=300, z_pos=1050, z_speed=300)
    # unilab:node_uuid=eb907f4e-fb96-4310-8368-a092c84b3680
    step_087 = roboticarmdevice.pick(item_type='plate', number=1, station='centrifuge', timeout=30000, x_speed=300)
    # unilab:node_uuid=7ff0c4df-3f58-4364-b24d-081be2646983
    step_088 = roboticarmdevice.place(item_type='plate', number=1, station='nine9320', timeout=30000, x_speed=300)
    # unilab:node_uuid=8f8991ef-73d7-416f-8a83-bebf9df72fd5
    step_089 = gn_prcxi9300handler.create_protocol()
    # unilab:node_uuid=a9123a10-a10a-4ea2-82e5-ebd09339fcf2
    step_090 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, force=5, from_=16, get_direction='0', hierarchy=1, intermediate_locations=[], pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=1, plate=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], put_down_position=1, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=10)
    # unilab:node_uuid=a053ffb0-6ee6-4b97-ab2d-4eac21970ed5
    step_091 = gn_prcxi9300handler.transfer_liquid(asp_flow_rates=[300], asp_vols=[500], blow_out_air_volume=[20], blow_out_air_volume_before=None, delays=None, dis_flow_rates=[300], dis_vols=[500], dispensing_method=[], hover_below_liquid_level=[], is_96_well=True, liquid_height=None, mix_liquid_height=None, mix_rate=None, mix_stage='none', mix_times=None, mix_vol=None, none_keys=[], offsets=None, post_air_volume=[], post_discharge_pause_time_ms=[], source_slots=16, sources=[{'uuid': '8e1cb526-32c4-4c6c-bb22-6faa81e209c5'}], spread='wide', target_slots=5, targets=[{'uuid': '53aca277-6dea-43a5-89d2-83b15a4a01b7'}], tip_rack_slots=12, tip_racks=[{'uuid': '0014ea7c-9743-452a-96ab-5407f475a32c'}], touch_tip=False, use_channels=[], z_start_point_offset_height=[])
    # unilab:node_uuid=352a4321-2654-487f-94f4-e4a20a519626
    step_092 = gn_prcxi9300handler.transfer_liquid(asp_flow_rates=[300], asp_vols=[500], blow_out_air_volume=[20], blow_out_air_volume_before=None, delays=None, dis_flow_rates=[300], dis_vols=[500], dispensing_method=[], hover_below_liquid_level=[], is_96_well=True, liquid_height=None, mix_liquid_height=None, mix_rate=None, mix_stage='none', mix_times=None, mix_vol=None, none_keys=[], offsets=None, post_air_volume=[], post_discharge_pause_time_ms=[], source_slots=8, sources=[{'uuid': '39051c8a-0180-4516-88e1-fa7ec724f453'}], spread='wide', target_slots=16, targets=[{'uuid': '8e1cb526-32c4-4c6c-bb22-6faa81e209c5'}], tip_rack_slots=11, tip_racks=[{'uuid': '2c525d62-d238-48d5-892a-993e5c0065d7'}], touch_tip=False, use_channels=[], z_start_point_offset_height=[])
    # unilab:node_uuid=b524fef9-776f-42c0-b92c-e3b362b03809
    step_093 = gn_prcxi9300handler.transfer_liquid(asp_flow_rates=[300], asp_vols=[150], blow_out_air_volume=[20], blow_out_air_volume_before=None, delays=None, dis_flow_rates=[300], dis_vols=[150], dispensing_method=[], hover_below_liquid_level=[], is_96_well=True, liquid_height=None, mix_liquid_height=None, mix_rate=None, mix_stage='none', mix_times=None, mix_vol=None, none_keys=[], offsets=None, post_air_volume=[], post_discharge_pause_time_ms=[], source_slots=16, sources=[{'uuid': '8e1cb526-32c4-4c6c-bb22-6faa81e209c5'}], spread='wide', target_slots=4, targets=[{'uuid': '75874461-548a-41db-bc44-a6ce51fb0e26'}], tip_rack_slots=15, tip_racks=[{'uuid': '695548f2-3e97-4913-a2c5-e19b3b07cac9'}], touch_tip=False, use_channels=[], z_start_point_offset_height=[])
    # unilab:node_uuid=a521f1bd-f022-4b20-97cc-19a39b39a582
    step_094 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, force=5, from_=10, get_direction='0', hierarchy=1, intermediate_locations=[], pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=1, plate=[{'uuid': '8e1cb526-32c4-4c6c-bb22-6faa81e209c5'}], put_down_position=1, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=16)
    # unilab:node_uuid=14daa32c-2b4a-4d46-befd-1d7ecc8d6268
    step_095 = gn_prcxi9300handler.run_protocol()
    # unilab:node_uuid=b85b4ccb-09ba-41be-ad28-d989eaea0497
    step_096 = roboticarmdevice.pick(item_type='plate', number=1, station='nine9320', timeout=30000, x_speed=300)
    # unilab:node_uuid=6884f0b6-f532-44df-b9ee-193ff49a671c
    step_097 = roboticarmdevice.place(item_type='plate', number=1, station='centrifuge', timeout=30000, x_speed=300)
    # unilab:node_uuid=b9583617-edc6-411e-96f7-035c917ce710
    step_098 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=42ac759e-9047-4ce6-8350-bb663704f470
    step_099 = centrifugedevice.execute_command(cmd_type=5, inner_z_pos=3590, plate_no=1, rpm=0, time_minutes=1, timeout=180, y_pos=-2160, y_speed=300, z_pos=1050, z_speed=300)
    # unilab:node_uuid=1d9333f4-45f9-4a05-910d-10fa9d105497
    step_100 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=c1634928-9b47-4287-bf1a-ba9e44c179f6
    step_101 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, drop_direction='', force=5, from_=15, get_direction='', hierarchy=1, intermediate_locations=[], pickup_direction='', pickup_distance_from_top=0, pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=2, plate=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], put_direction='', put_down_position=2, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=16)
    # unilab:node_uuid=81791460-0ae1-435b-970d-2d3552a1063c
    step_102 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=adfb3e33-b62c-4a24-b1c5-6152501d612e
    step_103 = roboticarmdevice.pick(item_type='plate', number=1, station='nine9320', timeout=180, x_speed=300)
    # unilab:node_uuid=bad4b187-bd2a-436a-a084-82d3823f0a12
    step_104 = roboticarmdevice.place(item_type='plate', number=70, station='stack', timeout=180, x_speed=300)
    # unilab:node_uuid=08bd3dc2-28b5-4c3f-b0a5-ff5fed308dd7
    step_105 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=6a1823b5-7f89-4e76-bfd7-82f0c6692573
    step_106 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, drop_direction='', force=5, from_=12, get_direction='', hierarchy=1, intermediate_locations=[], pickup_direction='', pickup_distance_from_top=0, pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=2, plate=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], put_direction='', put_down_position=2, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=16)
    # unilab:node_uuid=44a4874b-cfc4-4846-a00b-1e349b002d26
    step_107 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=9905e813-61e5-48fb-ad19-ded59dba5f25
    step_108 = roboticarmdevice.pick(item_type='plate', number=1, station='nine9320', timeout=180, x_speed=300)
    # unilab:node_uuid=3e10785b-12f8-4af8-b9e1-95bfe5ebe072
    step_109 = roboticarmdevice.place(item_type='plate', number=71, station='stack', timeout=180, x_speed=300)
    # unilab:node_uuid=ae02aac3-195c-4f27-86fd-a5757a360b4a
    step_110 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=4c1ecd55-479b-4df1-ba97-c02425728fc1
    step_111 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, drop_direction='', force=5, from_=11, get_direction='', hierarchy=1, intermediate_locations=[], pickup_direction='', pickup_distance_from_top=0, pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=2, plate=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], put_direction='', put_down_position=2, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=16)
    # unilab:node_uuid=42dd40f0-3f34-4485-b785-ff8de85b79cf
    step_112 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=a5685076-7bf4-4f87-8c9a-1fddabb5ec7a
    step_113 = roboticarmdevice.pick(item_type='plate', number=1, station='nine9320', timeout=180, x_speed=300)
    # unilab:node_uuid=3829dc1c-496a-413a-ae5e-80b5a2741073
    step_114 = roboticarmdevice.place(item_type='plate', number=72, station='stack', timeout=180, x_speed=300)
    # unilab:node_uuid=1559e17d-523e-4532-a907-dc861a538ecc
    step_115 = roboticarmdevice.pick(item_type='plate', number=73, station='stack', timeout=180, x_speed=300)
    # unilab:node_uuid=b5055183-b079-4c87-bdb8-ce4dc126da6a
    step_116 = roboticarmdevice.place(item_type='plate', number=1, station='nine9320', timeout=180, x_speed=300)
    # unilab:node_uuid=757fed2c-e916-4ffb-9ef0-059256d540a8
    step_117 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=1b16a275-94d0-4e66-9d65-10ac73edf651
    step_118 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, drop_direction='', force=5, from_=16, get_direction='', hierarchy=1, intermediate_locations=[], pickup_direction='', pickup_distance_from_top=0, pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=2, plate=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], put_direction='', put_down_position=2, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=11)
    # unilab:node_uuid=f52cfb97-b5d4-49e5-9481-e023b44454fc
    step_119 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=4330153e-6f1e-4539-b16d-56c628e40c3c
    step_120 = roboticarmdevice.pick(item_type='plate', number=74, station='stack', timeout=180, x_speed=300)
    # unilab:node_uuid=3b13ef6c-502a-43a3-bb96-22716e621f41
    step_121 = roboticarmdevice.place(item_type='plate', number=1, station='nine9320', timeout=180, x_speed=300)
    # unilab:node_uuid=858befdc-d391-4368-a0dd-a5fb737160e5
    step_122 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=9d0d63ed-d0cd-4460-9788-4bb679e8ed23
    step_123 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, drop_direction='', force=5, from_=16, get_direction='', hierarchy=1, intermediate_locations=[], pickup_direction='', pickup_distance_from_top=0, pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=2, plate=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], put_direction='', put_down_position=2, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=12)
    # unilab:node_uuid=27cdfb61-426e-4d6c-b492-5b9ef9aeb147
    step_124 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=a0a85047-ef7a-48d7-96c6-6f816f6b3430
    step_125 = roboticarmdevice.pick(item_type='plate', number=75, station='stack', timeout=180, x_speed=300)
    # unilab:node_uuid=9461cf7b-d5bf-48b6-8d0a-0344d7db8c3e
    step_126 = roboticarmdevice.place(item_type='plate', number=1, station='nine9320', timeout=180, x_speed=300)
    # unilab:node_uuid=e50f7147-becd-4252-80a4-a02b9ffa6ec7
    step_127 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=1118c8c1-b9d3-4c7d-8ab6-9977e6ed04fe
    step_128 = gn_prcxi9300handler.move_plate(destination_offset={'x': 0, 'y': 0, 'z': 0}, drop_direction='', force=5, from_=16, get_direction='', hierarchy=1, intermediate_locations=[], pickup_direction='', pickup_distance_from_top=0, pickup_offset={'x': 0, 'y': 0, 'z': 0}, pinch_it_up_position=2, plate=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], put_direction='', put_down_position=2, resource_offset={'x': 0, 'y': 0, 'z': 0}, to=15)
    # unilab:node_uuid=03b1c848-28eb-4dd8-b0c5-5cc582587301
    step_129 = gn_prcxi9300handler.run_protocol(protocol_id=None)
    # unilab:node_uuid=f9a55fa6-a22a-4abd-8aa4-9db21050a927
    step_130 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=0745e4bb-1c9e-4c53-8a18-90154140695c
    step_131 = centrifugedevice.execute_command(cmd_type=7, inner_z_pos=3590, plate_no=3, rpm=0, time_minutes=0, timeout=120, y_pos=-2160, y_speed=300, z_pos=1050, z_speed=300)
    # unilab:node_uuid=e487c38b-aa7f-4dd1-8cea-2eaa9f111301
    step_132 = roboticarmdevice.pick(item_type='plate', number=1, station='centrifuge', timeout=30000, x_speed=300)
    # unilab:node_uuid=f7b3a1ba-5be2-4207-b5d8-a69febf96314
    step_133 = roboticarmdevice.place(item_type='plate', number=1, station='nine9320', timeout=30000, x_speed=300)
    # unilab:node_uuid=019ef012-5ab4-4ac7-aa88-3aeec1bb1da9
    step_134 = gn_prcxi9300handler.create_protocol()
    # unilab:node_uuid=608d2fcd-c7f8-4627-8353-d0106a190a29
    step_135 = gn_prcxi9300handler.transfer_liquid(asp_flow_rates=[100], asp_vols=[150], blow_out_air_volume=[20], blow_out_air_volume_before=[], delays=None, dis_flow_rates=[100], dis_vols=[150], dispensing_method=[], hover_below_liquid_level=[], is_96_well=True, liquid_height=None, mix_liquid_height=None, mix_rate=None, mix_stage='none', mix_times=None, mix_vol=None, none_keys=[], offsets=None, post_air_volume=[], post_discharge_pause_time_ms=[], source_slots=16, sources=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], spread='wide', target_slots=7, targets=[{'uuid': 'bd6f7d88-e96a-4efa-b8c6-f5514aeb7cdf'}], tip_rack_slots=15, tip_racks=[{'uuid': '695548f2-3e97-4913-a2c5-e19b3b07cac9'}], touch_tip=False, use_channels=[], z_start_point_offset_height=[])
    # unilab:node_uuid=799a0290-d6b3-48f8-b780-74f93fce2a04
    step_136 = gn_prcxi9300handler.run_protocol()
    # unilab:node_uuid=abc07f06-d5a7-469f-bada-969fa097b75e
    step_137 = roboticarmdevice.pick(item_type='plate', number=1, station='nine9320', timeout=30000, x_speed=300)
    # unilab:node_uuid=0dc8717f-5f4b-4c28-9084-3291b5cd82a0
    step_138 = roboticarmdevice.place(item_type='plate', number=1, station='centrifuge', timeout=30000, x_speed=300)
    # unilab:node_uuid=53381399-5144-48e0-8709-f7fd1ae86f11
    step_139 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=15d2809d-e1f9-4847-a1f7-b785ba87abc1
    step_140 = centrifugedevice.execute_command(cmd_type=5, inner_z_pos=3590, plate_no=3, rpm=0, time_minutes=1, timeout=180, y_pos=-2160, y_speed=300, z_pos=1050, z_speed=300)
    # unilab:node_uuid=be73c63c-0b82-4349-ba1d-832d120c8b74
    step_141 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=bed74a1d-dc8f-4275-b1dd-c5ce151e47b7
    step_142 = centrifugedevice.execute_command(cmd_type=6, plate_no=1, rpm=4000, temperature=20, time_minutes=30, timeout=2500)
    # unilab:node_uuid=71faaeaa-b958-490c-b94a-b55fc986e8d0
    step_143 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=c88775f4-92b1-4305-bba2-d9ac90ee889a
    step_144 = centrifugedevice.execute_command(cmd_type=7, inner_z_pos=3590, plate_no=1, rpm=0, time_minutes=0, timeout=120, y_pos=-2160, y_speed=300, z_pos=1050, z_speed=300)
    # unilab:node_uuid=69c981a8-d1cc-444e-a3b4-750305c94866
    step_145 = roboticarmdevice.pick(item_type='plate', number=1, station='centrifuge', timeout=30000, x_speed=300)
    # unilab:node_uuid=7d68382b-9f9d-4af7-a2ac-9c52ee3859ba
    step_146 = roboticarmdevice.place(item_type='plate', number=1, station='nine9320', timeout=30000, x_speed=300)
    # unilab:node_uuid=8343b40e-cede-4833-ac79-83164c68c21a
    step_147 = gn_prcxi9300handler.create_protocol(none_keys=[], protocol_author='', protocol_date='', protocol_description='', protocol_name='', protocol_type='', protocol_version='')
    # unilab:node_uuid=384df466-8711-454c-9551-ed08cd81f534
    step_148 = gn_prcxi9300handler.transfer_liquid(asp_flow_rates=[100], asp_vols=[500], blow_out_air_volume=[20], blow_out_air_volume_before=[], delays=[], dis_flow_rates=[100], dis_vols=[500], dispensing_method=[], hover_below_liquid_level=[], is_96_well=True, liquid_height=[], mix_stage='none', none_keys=[], offsets=[], post_air_volume=[], post_discharge_pause_time_ms=[], source_slots=16, sources=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], spread='wide', target_slots=5, targets=[{'uuid': '53aca277-6dea-43a5-89d2-83b15a4a01b7'}], tip_rack_slots=12, tip_racks=[{'uuid': '2c525d62-d238-48d5-892a-993e5c0065d7'}], touch_tip=False, use_channels=[], z_start_point_offset_height=[])
    # unilab:node_uuid=0db0f546-ee3f-486a-94f9-ac2ac84ee3f0
    step_149 = gn_prcxi9300handler.transfer_liquid(asp_flow_rates=[100], asp_vols=[500], blow_out_air_volume=[20], blow_out_air_volume_before=[], delays=[], dis_flow_rates=[100], dis_vols=[500], dispensing_method=[], hover_below_liquid_level=[], is_96_well=True, liquid_height=[], mix_stage='none', none_keys=[], offsets=[], post_air_volume=[], post_discharge_pause_time_ms=[], source_slots=16, sources=[{'uuid': '39051c8a-0180-4516-88e1-fa7ec724f453'}], spread='wide', target_slots=5, targets=[{'uuid': 'e74c153c-a29f-4653-a665-b9e1cc1711a9'}], tip_rack_slots=12, tip_racks=[{'uuid': '0014ea7c-9743-452a-96ab-5407f475a32c'}], touch_tip=False, use_channels=[], z_start_point_offset_height=[])
    # unilab:node_uuid=ef2f9edf-a45f-48a0-82ae-30fb4b76cea5
    step_150 = gn_prcxi9300handler.run_protocol()
    # unilab:node_uuid=a4e99c1d-8eba-4714-8785-568bba60a708
    step_151 = roboticarmdevice.pick(item_type='plate', number=1, station='nine9320', timeout=30000, x_speed=300)
    # unilab:node_uuid=f1b8ff90-123c-431c-bffd-380a2e468524
    step_152 = roboticarmdevice.place(item_type='plate', number=1, station='finished_area', timeout=30000, x_speed=300)
    # unilab:node_uuid=93fd3c6f-714e-4791-90a3-b4fc609f3309
    step_153 = centrifugedevice.execute_command(cmd_type=8, inner_z_pos=0, plate_no=0, rpm=0, time_minutes=0, timeout=120, y_pos=0, y_speed=0, z_pos=0, z_speed=0)
    # unilab:node_uuid=931e321f-ae57-455d-9376-ed49819c7d45
    step_154 = centrifugedevice.execute_command(cmd_type=7, inner_z_pos=3590, plate_no=3, rpm=0, time_minutes=0, timeout=120, y_pos=-2160, y_speed=300, z_pos=1050, z_speed=300)
    # unilab:node_uuid=d67b0bc9-36f0-4565-9640-a8531f9a8cd7
    step_155 = roboticarmdevice.pick(item_type='plate', number=1, station='centrifuge', timeout=30000, x_speed=300)
    # unilab:node_uuid=ff44545b-0b88-4d15-b645-078fed6926c0
    step_156 = roboticarmdevice.place(item_type='plate', number=5, station='stack', timeout=30000, x_speed=300)
    return workflow_output()
