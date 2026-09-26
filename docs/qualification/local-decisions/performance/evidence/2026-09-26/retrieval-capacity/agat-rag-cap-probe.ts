import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';
import { writeFileSync } from 'node:fs';
import { AgatStore } from '/Users/mdavliatshin/Documents/Workspace/Agat/apps/coordinator/src/database.ts';
const store = new AgatStore(':memory:', {seedDemo:false});
try {
 const node = store.registerNode({enrollmentToken:'fixture',name:'cap-probe',platform:'test',models:['test-model'],embeddingModels:['fixture-embedding'],maxConcurrency:1}).id;
 const collection = store.createKnowledgeCollection({name:'Synthetic candidate boundary',embeddingModel:'fixture-embedding',chunkSize:400,chunkOverlap:0,topK:1}) as {id:string};
 const target = store.ingestKnowledgeDocument(collection.id,{name:'Old exact match',content:'Synthetic exact-match record; no business facts.',sourceUri:'urn:agat:cap-probe:target'}) as {id:string};
 let batches=0;
 function indexPending() {
  for(let next;(next=store.leaseKnowledgeEmbedding(node));) {
   store.completeKnowledgeEmbedding(node,next.leaseId,next.chunks.map(chunk=>({chunkId:chunk.id,embedding:next.document.id===target.id?[1,0]:[0,1]})));
   batches++;
  }
 }
 indexPending();
 store.db.prepare("UPDATE knowledge_chunks SET embedded_at = '2026-01-01T00:00:00.000Z' WHERE document_id = ?").run(target.id);
 const filler = store.ingestKnowledgeDocument(collection.id,{name:'Synthetic orthogonal fillers',content:'A'.repeat(4999*400),sourceUri:'urn:agat:cap-probe:filler'}) as {chunkCount:number};
 assert.equal(filler.chunkCount,4999);indexPending();
 const results=[];
 function probe(expected:number) {
  const count=Number((store.db.prepare('SELECT COUNT(*) AS count FROM knowledge_chunks WHERE embedding_json IS NOT NULL').get() as any).count);assert.equal(count,expected);
  const run=store.createRun({name:`Candidate cap ${count}`,input:'Synthetic exact vector query',agentIds:['collector'],approvalRequired:false,knowledgeCollectionIds:[collection.id]});
  const lease=store.leaseNext(node)!;assert.equal(lease.run.id,run.id);
  const before=performance.now();
  const response=store.searchKnowledge(node,lease.leaseId,{queries:[{embeddingModel:'fixture-embedding',collectionIds:[collection.id],topK:1,vector:[1,0]}]});
  results.push({indexedChunks:count,returnedScore:response.hits[0]?.score,exactTargetReturned:response.hits[0]?.provenance.documentId===target.id,elapsedMs:Number((performance.now()-before).toFixed(3))});
  store.completeLease(node,lease.leaseId,'Synthetic fixture completion');
 }
 probe(5000);
 store.ingestKnowledgeDocument(collection.id,{name:'Newest extra filler',content:'Additional synthetic orthogonal record.',sourceUri:'urn:agat:cap-probe:extra'});indexPending();probe(5001);
 const result={schemaVersion:'agat.rag.candidate-cap-probe.v1',classification:'synthetic-vectors-diagnostic',embeddingDimensions:2,embeddingBatches:batches,results,qualifiedForRouting:false};
 assert.equal(results[0].exactTargetReturned,true);assert.equal(results[1].exactTargetReturned,false);
 writeFileSync('/tmp/agat-rag-cap-probe.json',JSON.stringify(result,null,2)+'\n',{flag:'wx'});console.log(JSON.stringify(result));
} finally {store.close();}
