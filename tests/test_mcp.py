import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from mcp import Client
from kiravto.mcp_server import server
from kiravto.domain import Store

class McpTests(unittest.TestCase):
    def test_real_client_tools_call_and_propose(self):
        async def scenario():
            with tempfile.TemporaryDirectory(dir='data') as folder:
                path=str(Path(folder)/'mcp.db')
                db=Store(path)
                db.ingest([{'id':'test-car','name':'Test car','price':100,'url':'https://example.com'}],now='2026-10-22T12:00:00+00:00')
                db.close()
                from kiravto.observability import record
                record(path,{'id':1,'listing_evidence':{'private-test-proof':{'signature':'x'*10000}},'detail_evidence':{'private-test-proof':{}},'partition_evidence':[{'ids':['private-test-proof']}],'category_proof':{'verified':True,'evidence':[{'ids':['private-test-proof']}]},'limitations':[]},notify=False)
                with patch.dict(os.environ,{'DB_PATH':path}):
                    async with Client(server) as client:
                        listed=await client.list_tools()
                        names={x.name for x in listed.tools}
                        self.assertEqual(len(names),7)
                        self.assertFalse(any('publish' in x for x in names))
                        status=await client.call_tool('run_status',{'limit':1})
                        status_data=json.loads(status.content[0].text)
                        self.assertEqual(len(status_data['runs']),1)
                        self.assertNotIn('private-test-proof',str(status))
                        self.assertEqual(status_data['runs'][0]['category_proof']['evidence_page_count'],1)
                        self.assertEqual(status_data['runs'][0]['partition_node_count'],1)
                        # Compact transport must not delete stored evidence.
                        check=Store(path)
                        try:
                            self.assertIn('private-test-proof',check.db.execute('SELECT payload FROM run_metrics').fetchone()[0])
                        finally:check.close()
                        result=await client.call_tool('stock_summary',{})
                        self.assertFalse(result.is_error)
                        proposed=await client.call_tool('propose_post',{'offer_id':'test-car'})
                        self.assertFalse(proposed.is_error)
                        self.assertIn('pending',str(proposed))
        asyncio.run(scenario())
