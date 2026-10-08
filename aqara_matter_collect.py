"""Collect M3 Matter events and periodic cached snapshots through a local WebSocket."""
import asyncio
import json
import logging
import time

import aiohttp

import aqara_local


async def collect() -> None:
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect('http://127.0.0.1:5580/ws', heartbeat=30) as ws:
            await ws.send_json({'message_id':'initial','command':'start_listening','args':{}})
            nodes = {}
            last_refresh = time.monotonic()
            while True:
                try:
                    message = await ws.receive(timeout=5)
                except asyncio.TimeoutError:
                    message = None
                if message is not None:
                    if message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                        raise ConnectionError('Matter connection closed')
                    if message.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(message.data)
                        event = data.get('event')
                        if data.get('message_id') in ('initial','snapshot') and 'result' in data:
                            for node in data['result']:
                                nodes[node['node_id']] = node
                                count = aqara_local.record_node(node, 'matter_snapshot')
                                print(json.dumps({'event':'aqara_snapshot','node_id':node['node_id'],'sensors':count}),flush=True)
                        elif event in ('node_updated','node_added'):
                            node = data['data']; nodes[node['node_id']] = node
                            aqara_local.record_node(node, 'matter_event')
                        elif event == 'attribute_updated':
                            node_id, path, value = data['data']
                            if node_id in nodes:
                                nodes[node_id]['attributes'][path] = value
                                if path.endswith(('/1026/0','/1029/0','/47/12','/57/17')):
                                    aqara_local.record_node(nodes[node_id], 'matter_event')
                                    print(json.dumps({'event':'aqara_attribute','node_id':node_id,'path':path}),flush=True)
                if time.monotonic()-last_refresh >= 60:
                    await ws.send_json({'message_id':'snapshot','command':'get_nodes','args':{}})
                    last_refresh = time.monotonic()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            asyncio.run(collect())
        except Exception as exc:
            logging.warning('Aqara local collector disconnected (%s); retrying in 10 seconds', type(exc).__name__)
        time.sleep(10)
