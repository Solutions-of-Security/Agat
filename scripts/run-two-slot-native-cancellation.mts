import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { parseArgs } from "node:util";
import { AgatStore } from "../apps/coordinator/src/database.ts";
import { loadConfig } from "../apps/coordinator/src/config.ts";
import { createCoordinatorServer } from "../apps/coordinator/src/server.ts";
import type { ProcessGraph } from "../apps/coordinator/src/types.ts";

const { values }=parseArgs({options:{"decision-url":{type:"string"},"evidence-dir":{type:"string"},"primary-url":{type:"string"}}});
assert.ok(values["decision-url"] && values["evidence-dir"]);
const directory=path.resolve(values["evidence-dir"]),url=new URL(values["decision-url"]);
assert.ok(directory.startsWith(path.resolve("docs/private")+path.sep));
assert.ok(url.protocol==="http:" && url.hostname==="127.0.0.1" && url.port && !["8766","9095","11434"].includes(url.port)
  && url.pathname==="/" && !url.username && !url.password && !url.search && !url.hash);
const plan=JSON.parse(fs.readFileSync(path.join(directory,"plan.json"),"utf8")),spec=plan.protocol;
const isDeadline=plan.schemaVersion==="agat.decision.two-slot-deadline-plan.v1";
const isRealPrimary=plan.schemaVersion==="agat.decision.two-slot-real-primary-cancellation-plan.v1";
assert.ok(isDeadline || isRealPrimary || plan.schemaVersion==="agat.decision.two-slot-cancellation-plan.v1");
const primaryNativeUrl=isRealPrimary?new URL(values["primary-url"]!):undefined;
if(primaryNativeUrl)assert.ok(primaryNativeUrl.protocol==="http:" && primaryNativeUrl.hostname==="127.0.0.1" && primaryNativeUrl.port
  && !["8766","9095","11434",url.port].includes(primaryNativeUrl.port) && primaryNativeUrl.pathname==="/" && !primaryNativeUrl.username && !primaryNativeUrl.password && !primaryNativeUrl.search && !primaryNativeUrl.hash);
else assert.equal(values["primary-url"],undefined);
assert.equal(spec.kind,isDeadline?"worker_deadline_while_peer_primary_in_flight":"cancel_active_native_task_while_peer_primary_in_flight");
if(isDeadline){assert.equal(spec.targetCallerTimeoutMs,250);assert.equal(spec.healthyCallerTimeoutMs,10000);assert.deepEqual(spec.startOrder,[0,2,1,3]);}
const preparedFile=isDeadline?"native-target-prepared.json":"coordinator-cancellation-prepared.json";
const readyFile=isDeadline?"active-native-ready.json":"coordinator-cancellation-ready.json",drainedFile=isDeadline?"active-native-drained.json":"coordinator-cancellation-drained.json";
assert.equal(spec.workerConcurrency,2);assert.equal(spec.globalMaxConcurrency,2);assert.equal(spec.localTargetIndex,1);assert.equal(spec.localPeerIndex,2);
assert.equal(spec.schedulerMode,"sequential");assert.equal(spec.peerHoldDeadlineMs,isRealPrimary?180000:30000);assert.equal(spec.driverDeadlineMs,180000);
assert.equal(spec.retryCount,0);assert.equal(spec.nativeFault.targetIndex,1);
const cases=spec.selectedOriginalIndices.map((i:number)=>plan.context.inputs[i]);assert.equal(cases.length,4);
if(isRealPrimary){
  const target=spec.selectedOriginalIndices[1],peer=plan.context.inputs.reduce((best:number,c:any,i:number)=>c.inputTokens>plan.context.inputs[best].inputTokens?i:best,0);
  assert.deepEqual(spec.selectedOriginalIndices,[target-1,target,peer,target+2]);assert.equal(new Set(spec.selectedOriginalIndices).size,4);
  assert.ok(cases[0].contextEligible && cases[1].contextEligible && cases[3].contextEligible && !cases[2].contextEligible);
  assert.equal(spec.primary,"pinned_qwen3_8b_actual_chat");assert.equal(spec.primaryNumParallel,2);assert.equal(spec.primaryTimeoutMs,180000);
  assert.equal(spec.primaryContextLength,32768);assert.equal(spec.primaryDecodeLimit,128);
}else assert.deepEqual(spec.selectedOriginalIndices,[0,1,2,3].map(i=>spec.selectedOriginalIndices[0]+i));
const digest=(raw:string|Buffer)=>createHash("sha256").update(raw).digest("hex");
const now=()=>new Date().toISOString();
const save=(name:string,value:unknown)=>fs.writeFileSync(path.join(directory,name),JSON.stringify(value,null,2)+"\n",{flag:"wx",mode:0o600});
const journal=(name:string,value:unknown)=>fs.appendFileSync(path.join(directory,name),JSON.stringify(value)+"\n",{mode:0o600});
const pin=(name:string)=>digest(fs.readFileSync(path.join(directory,name)));
const publish=(name:string,value:unknown)=>{const pending=path.join(directory,name+".pending");fs.writeFileSync(pending,JSON.stringify(value)+"\n",{flag:"wx",mode:0o600});
  try{fs.linkSync(pending,path.join(directory,name));}finally{fs.unlinkSync(pending);}};
const healthResponse=await fetch(url.origin+"/health",{signal:AbortSignal.timeout(5000),redirect:"error"});assert.equal(healthResponse.status,200);
const health=await healthResponse.json() as any;assert.equal(health.profileSha256,plan.context.profileSha256);
const config={mode:"shadow",profileJson:health.profileJson,timeoutMs:10000,...plan.config};
const store=new AgatStore(":memory:",{seedDemo:false,decisionShadowEnabled:true});store.updateModelRouterPolicy({enabled:false});store.updateScheduler("sequential",2);
const primaryModel=isRealPrimary?"qwen3:8b":"fixture-primary";
const agent=store.createAgent({name:isRealPrimary?"Two-slot actual primary":"Two-slot fixture primary",role:"Diagnostic only",model:primaryModel,systemPrompt:spec.systemPrompt});
const graph:ProcessGraph={nodes:[
  {id:"start",name:"Start",type:"start",position:{x:0,y:0},config:{}},
  {id:"agent",name:"Scoped primary and shadow",type:"agent",position:{x:200,y:0},config:{agentId:String(agent.id),decisionShadow:config}},
  {id:"end",name:"End",type:"end",position:{x:400,y:0},config:{}}],
  edges:[{id:"a",source:"start",target:"agent",branch:"default"},{id:"b",source:"agent",target:"end",branch:"default"}]};
const adminToken=randomUUID(),enrollmentToken=randomUUID();
const coordinator=createCoordinatorServer({...loadConfig(),host:"127.0.0.1",port:0,serveWeb:false,adminToken,enrollmentToken,
  oidcEnabled:false,mcpEnabled:false,sandboxEnabled:false,a2aEnabled:false,localWorkerLauncherEnabled:false},store);
let failure:string|undefined,worker:ReturnType<typeof spawn>|undefined,peerHeld:any,peerResponse:http.ServerResponse|undefined;
let resolvePeer:(()=>void)|undefined,peerReleased=false,peerClosed=false;
const primaryAbort=new AbortController();
const primaryRows:any[]=[],coordinatorRows:any[]=[],routes:any[]=[],runs:any[]=[],primaryStarted=new Set<number>();
const httpOrigin=performance.now();
const primary=http.createServer(async(req,res)=>{
  if(req.method!=="POST" || req.url!=="/v1/chat/completions"){req.resume();res.writeHead(404).end();return;}
  const diagnosticRow:any={startedAt:now()};
  try{
    const startedAt=now(),started=performance.now();const chunks:Buffer[]=[];let size=0;
    for await(const chunk of req){size+=chunk.length;assert.ok(size<=128*1024);chunks.push(chunk);}
    const requestBody=Buffer.concat(chunks).toString("utf8"),request=JSON.parse(requestBody);
    Object.assign(diagnosticRow,{requestBody,requestBodySha256:digest(requestBody)});
    const indices=cases.map((c:any,i:number)=>request.messages.some((m:any)=>m.role==="user" && typeof m.content==="string" && m.content.includes(c.request.state))?i:-1).filter((i:number)=>i>=0);
    assert.equal(indices.length,1);const index=indices[0];assert.ok(runs[index] && !primaryStarted.has(index));primaryStarted.add(index);
    Object.assign(diagnosticRow,{index,originalIndex:spec.selectedOriginalIndices[index],runId:runs[index].runId});
    assert.equal(request.messages[1].content.split(cases[index].request.state).length,2);
    if(isRealPrimary){
      assert.deepEqual(request,{model:primaryModel,messages:[{role:"system",content:spec.systemPrompt},{role:"user",content:"Задача: "+spec.processName+"\n\nВходные данные:\n"+cases[index].request.state}],stream:false,temperature:.2});
      const nativeRequestBody=JSON.stringify({model:primaryModel,messages:request.messages,stream:false,keep_alive:"5m",...plan.primary.generation});
      const nativeStartedAt=now();
      Object.assign(diagnosticRow,{nativeStartedAt,nativeUrl:primaryNativeUrl!.origin,nativeRequestBody,nativeRequestBodySha256:digest(nativeRequestBody)});
      if(index===2){
        assert.ok(!peerHeld);peerResponse=res;
        req.socket.once("close",()=>{if(!peerReleased){peerClosed=true;failure??="Actual primary peer socket closed before native response";}});
        peerHeld={kind:"actual_pending_native_primary",index,originalIndex:spec.selectedOriginalIndices[index],runId:runs[index].runId,
          heldAt:now(),requestBodySha256:digest(requestBody),nativeStartedAt,nativeUrl:primaryNativeUrl!.origin,
          nativeRequestBodySha256:digest(nativeRequestBody),responseBytesWritten:0};save("peer-primary-held.json",peerHeld);
      }
      const nativeResponse=await fetch(primaryNativeUrl!.origin+"/api/chat",{method:"POST",headers:{"content-type":"application/json"},body:nativeRequestBody,
        signal:AbortSignal.any([primaryAbort.signal,AbortSignal.timeout(spec.primaryTimeoutMs)]),redirect:"error"});
      const nativeResponseReceivedAt=now(),nativeResponseBody=await nativeResponse.text();
      Object.assign(diagnosticRow,{nativeResponseReceivedAt,nativeHttpStatus:nativeResponse.status,nativeResponseBody,nativeResponseBodySha256:digest(nativeResponseBody)});
      assert.equal(nativeResponse.status,200);assert.ok(Buffer.byteLength(nativeResponseBody)<=128*1024);
      const native=JSON.parse(nativeResponseBody);assert.equal(native.model,primaryModel);assert.equal(native.done,true);
      assert.equal(native.message.role,"assistant");assert.ok(typeof native.message.content==="string" && native.message.content.trim());
      assert.ok(!native.message.thinking && !native.message.tool_calls);assert.ok(["stop","length"].includes(native.done_reason));
      assert.ok(Number.isInteger(native.prompt_eval_count) && native.prompt_eval_count>0 && native.prompt_eval_count<spec.primaryContextLength-spec.primaryDecodeLimit);
      assert.ok(Number.isInteger(native.eval_count) && native.eval_count>0 && native.eval_count<=spec.primaryDecodeLimit);
      if(index===2){
        assert.ok(fs.existsSync(path.join(directory,"native-recovered.json")),"Actual primary peer answered before native recovery");
        assert.ok(fs.existsSync(path.join(directory,"trace-peer-before-release.http.json")),"Actual primary peer answered before pending-peer trace");
        const recovered=JSON.parse(fs.readFileSync(path.join(directory,"native-recovered.json"),"utf8"));
        assert.ok(Date.parse(nativeResponseReceivedAt)>=Date.parse(recovered.appliedAt));assert.ok(!peerClosed && !res.destroyed);
        peerReleased=true;save("peer-primary-released.json",{kind:"actual_native_primary_response",index,runId:runs[index].runId,releasedAt:now(),nativeResponseReceivedAt,
          nativeResponseBodySha256:digest(nativeResponseBody),socketClosedBeforeRelease:peerClosed,socketOpenAtRelease:!res.destroyed,responseBytesWrittenBeforeRelease:0,
          recoveredFileSha256:pin("native-recovered.json"),heldFileSha256:pin("peer-primary-held.json")});
      }
      const responseBody=JSON.stringify({choices:[{message:{role:"assistant",content:native.message.content},finish_reason:native.done_reason}],
        usage:{prompt_tokens:native.prompt_eval_count,completion_tokens:native.eval_count}});
      const row={index,originalIndex:spec.selectedOriginalIndices[index],runId:runs[index].runId,startedAt,completedAt:now(),elapsedMs:performance.now()-started,
        httpStatus:200,nativeHttpStatus:nativeResponse.status,nativeUrl:primaryNativeUrl!.origin,nativeStartedAt,nativeResponseReceivedAt,
        outputSha256:digest(native.message.content.trim()),requestBody,requestBodySha256:digest(requestBody),nativeRequestBody,nativeRequestBodySha256:digest(nativeRequestBody),
        nativeResponseBody,nativeResponseBodySha256:digest(nativeResponseBody),responseBody,responseBodySha256:digest(responseBody)};
      primaryRows.push(row);journal("primary-http.jsonl",row);res.writeHead(200,{"content-type":"application/json"}).end(responseBody);return;
    }
    if(index===2){
      assert.ok(!peerHeld);peerResponse=res;
      req.socket.once("close",()=>{if(!peerReleased){peerClosed=true;failure??="Peer primary socket closed before recovery release";}});
      peerHeld={index,originalIndex:spec.selectedOriginalIndices[index],runId:runs[index].runId,heldAt:now(),requestBodySha256:digest(requestBody),responseBytesWritten:0};save("peer-primary-held.json",peerHeld);
      await new Promise<void>((resolve,reject)=>{resolvePeer=resolve;const timer=setTimeout(()=>reject(new Error("Held peer primary deadline exceeded")),spec.peerHoldDeadlineMs);
        const complete=resolvePeer;resolvePeer=()=>{clearTimeout(timer);complete();};});
      assert.ok(peerReleased && !peerClosed && !res.destroyed,"Peer primary was not preserved through release");
    }
    const responseBody=JSON.stringify({choices:[{message:{role:"assistant",content:"PRIMARY_OUTPUT"},finish_reason:"stop"}],usage:{prompt_tokens:1,completion_tokens:1}});
    const row={index,originalIndex:spec.selectedOriginalIndices[index],runId:runs[index].runId,startedAt,completedAt:now(),httpStatus:200,
      requestBody,requestBodySha256:digest(requestBody),responseBody,responseBodySha256:digest(responseBody)};
    primaryRows.push(row);journal("primary-http.jsonl",row);res.writeHead(200,{"content-type":"application/json"}).end(responseBody);
  }catch(error){failure??=String(error);journal("primary-errors.jsonl",{...diagnosticRow,error:String(error),at:now()});if(!res.destroyed)res.writeHead(502).end();}
});
coordinator.prependListener("request",(req:http.IncomingMessage,res:http.ServerResponse)=>{
  if(req.method!=="POST" || !/^\/api\/v1\/leases\/[^/]+\/(renew|decision-shadow(?:\/intent)?|complete|fail)$/.test(req.url??""))return;
  const row:any={method:"POST",path:req.url,startedAt:now(),startedMs:performance.now()-httpOrigin,requestBody:"",requestBodyComplete:false};let size=0;const chunks:Buffer[]=[];
  req.on("data",(chunk:Buffer)=>{size+=chunk.length;assert.ok(size<=128*1024);chunks.push(chunk);row.requestBody=Buffer.concat(chunks).toString("utf8");});
  req.on("end",()=>{row.requestBodyComplete=true;});res.once("finish",()=>{const complete={...row,httpStatus:res.statusCode,finishedAt:now(),finishedMs:performance.now()-httpOrigin,requestBodySha256:digest(row.requestBody)};coordinatorRows.push(complete);journal("coordinator-http.jsonl",complete);});
});
async function listen(server:http.Server){await new Promise<void>((resolve,reject)=>{server.once("error",reject);server.listen(0,"127.0.0.1",resolve);});
  const a=server.address();assert.ok(a && typeof a==="object" && ![8766,9095,11434].includes(a.port));return `http://127.0.0.1:${a.port}`;}
async function close(server:http.Server){if(server.listening){server.closeAllConnections();await new Promise<void>(resolve=>server.close(()=>resolve()));}}
const credential=path.join(directory,"worker-credentials.json"),log=fs.openSync(path.join(directory,"worker.log"),"wx",0o600);
async function stopWorker(){if(worker && worker.exitCode===null && worker.signalCode===null){const exited=new Promise<void>(resolve=>worker!.once("exit",()=>resolve()));worker.kill("SIGTERM");
  const timer=setTimeout(()=>{if(worker?.exitCode===null && worker.signalCode===null)worker.kill("SIGKILL");},5000);try{await exited;}finally{clearTimeout(timer);}}}
try{
  const primaryUrl=await listen(primary),coordinatorUrl=await listen(coordinator);
  const processId=String(store.createProcess({name:spec.processName,graph}).id);store.publishProcess(processId);save("workflow-graph.json",store.getProcessVersion(processId,1)!.graph);
  if(isDeadline){const targetGraph=structuredClone(graph);(targetGraph.nodes[1].config as any).decisionShadow.timeoutMs=spec.targetCallerTimeoutMs;
    store.updateProcess(processId,{name:spec.processName,graph:targetGraph});store.publishProcess(processId);save("workflow-target-graph.json",store.getProcessVersion(processId,2)!.graph);}
  const startAt=new Date(Date.now()+1000).toISOString();save("workflow-plan.json",{schemaVersion:plan.schemaVersion,protocol:spec,processId,processVersion:1,startAt,
    graphFileSha256:pin("workflow-graph.json"),...(isDeadline?{caseVersions:[1,2,1,1],targetGraphFileSha256:pin("workflow-target-graph.json")}:{}),
    endpoints:{primaryUrl,coordinatorUrl,decisionUrl:url.origin,...(isRealPrimary?{primaryNativeUrl:primaryNativeUrl!.origin}:{})}});
  const environment=Object.fromEntries(Object.entries(process.env).filter(([k])=>!k.startsWith("AGAT_") && !k.startsWith("OTEL_")));
  worker=spawn("python3",["workers/agat_worker.py","--coordinator",coordinatorUrl,"--credentials",credential,"--name",isDeadline?"two-slot-deadline-diagnostic":"two-slot-cancellation-diagnostic",
    "--models",primaryModel,"--model-url",primaryUrl+"/v1","--model-discovery","off","--no-web","--poll-interval","0.2","--concurrency","2","--decision-url",url.origin],
    {cwd:process.cwd(),stdio:["ignore",log,log],env:{...environment,AGAT_ENROLLMENT_TOKEN:enrollmentToken,OTEL_SDK_DISABLED:"true",NO_PROXY:"127.0.0.1,localhost"}});
  while(Date.now()<Date.parse(startAt))await new Promise(resolve=>setTimeout(resolve,Math.max(1,Date.parse(startAt)-Date.now())));
  const deadline=performance.now()+spec.driverDeadlineMs;
  async function until(predicate:()=>boolean,label:string,budget=30000){const limit=Math.min(deadline,performance.now()+budget);
    while(!predicate()){assert.ok(!failure,failure??"Owned fixture failed");assert.equal(worker!.exitCode,null);assert.equal(worker!.signalCode,null);
      assert.ok(performance.now()<limit,label);await new Promise(resolve=>setTimeout(resolve,10));}}
  async function receipt(name:string,budget=30000){await until(()=>fs.existsSync(path.join(directory,name)),"Receipt deadline "+name,budget);
    return JSON.parse(fs.readFileSync(path.join(directory,name),"utf8"));}
  async function trace(index:number,name:string){const endpoint=`/api/v1/runs/${runs[index].runId}/trace`;
    const response=await fetch(coordinatorUrl+endpoint,{headers:{"x-agat-admin-token":adminToken},signal:AbortSignal.timeout(5000),redirect:"error"});assert.equal(response.status,200);
    const raw=await response.text();assert.ok(Buffer.byteLength(raw)<=16*1024*1024);fs.writeFileSync(path.join(directory,name),raw,{flag:"wx",mode:0o600});
    journal("trace-http.jsonl",{index,path:endpoint,httpStatus:response.status,file:name,bodySha256:digest(raw),capturedAt:now()});return JSON.parse(raw);}
  function start(index:number){const version=isDeadline && index===1?2:1;const instance=store.startProcess(processId,{input:cases[index].request.state,version})!;runs[index]={instanceId:String(instance.id),runId:String(instance.runId)};}
  function route(index:number,t:any){const s=t.run.stages.find((s:any)=>s.processNodeId==="agent");assert.ok(s && s.attempt===1 && s.input===null);
    const row={index,originalIndex:spec.selectedOriginalIndices[index],caseId:cases[index].id,inputSha256:cases[index].inputSha256,...runs[index],stageId:s.id,runStatus:t.run.status};
    routes.push(row);journal("workflow-routes.jsonl",row);}
  start(0);await until(()=>store.getRun(runs[0].runId)!.status==="completed","Prefix did not complete",isRealPrimary?spec.primaryTimeoutMs:30000);route(0,await trace(0,"trace-prefix.http.json"));
  publish("native-prefix-ready.json",{runId:runs[0].runId,stageId:routes[0].stageId,traceFileSha256:pin("trace-prefix.http.json"),requestedAt:now()});
  await receipt("native-prefix-armed.json",10000);
  if(isDeadline){start(2);await until(()=>Boolean(peerHeld),"Peer primary was not held before target");start(1);}
  else{start(1);start(2);await until(()=>Boolean(peerHeld),"Peer primary was not held");}
  await until(()=>{const t=store.getRunTrace(runs[1].runId)! as any;return t.decisionCallerAccounting.stages.length===1 && t.decisionCallerAccounting.stages[0].assignments[0]?.intent===true;},"Target native intent was not pending",isRealPrimary?spec.primaryTimeoutMs:30000);
  const before=await trace(1,"trace-target-before.http.json"),peerBefore=await trace(2,"trace-peer-before.http.json");
  const targetStage=before.run.stages.find((s:any)=>s.processNodeId==="agent"),peerStage=peerBefore.run.stages.find((s:any)=>s.processNodeId==="agent");
  assert.equal(before.run.status,"running");assert.equal(peerBefore.run.status,"running");assert.equal(targetStage.nodeId,peerStage.nodeId);assert.ok(targetStage.nodeId);
  if(!isDeadline){const unauthorized=await fetch(coordinatorUrl+`/api/v1/runs/${runs[1].runId}/cancel`,{method:"POST",signal:AbortSignal.timeout(5000),redirect:"error"});assert.equal(unauthorized.status,401);await unauthorized.text();}
  publish(preparedFile,{targetIndex:1,caseId:cases[1].id,stageId:targetStage.id,runId:runs[1].runId,inputSha256:cases[1].inputSha256,profileSha256:plan.context.profileSha256,
    peerRunId:runs[2].runId,peerStageId:peerStage.id,workerNodeId:targetStage.nodeId,
    beforeTraceFileSha256:pin("trace-target-before.http.json"),peerTraceFileSha256:pin("trace-peer-before.http.json"),peerHeldFileSha256:pin("peer-primary-held.json"),preparedAt:now(),...(!isDeadline?{unauthenticatedStatus:401}:{})});
  const ready=await receipt(readyFile,15000);assert.equal(ready.stageId,targetStage.id);assert.equal(ready.upstreamResponseBytesObserved,0);
  if(!isDeadline)assert.ok(Date.now()-Date.parse(ready.activeObservedAt)<=250);assert.ok(!peerClosed && peerResponse && !peerResponse.destroyed);
  if(!isDeadline){
  const requestStartedAt=now(),cancelStarted=performance.now(),requestPath=`/api/v1/runs/${runs[1].runId}/cancel`,cancelResponse=await fetch(coordinatorUrl+requestPath,{method:"POST",headers:{"x-agat-admin-token":adminToken},signal:AbortSignal.timeout(5000),redirect:"error"});
  const responseBody=await cancelResponse.text();assert.equal(cancelResponse.status,204);assert.equal(responseBody,"");
  publish("coordinator-cancellation-applied.json",{targetIndex:1,caseId:cases[1].id,instanceId:runs[1].instanceId,stageId:targetStage.id,runId:runs[1].runId,inputSha256:cases[1].inputSha256,profileSha256:plan.context.profileSha256,
    requestStartedAt,responseCompletedAt:now(),cancelRequestElapsedMs:performance.now()-cancelStarted,requestMethod:"POST",requestPath,requestBody:"",requestBodySha256:digest(""),
    httpStatus:cancelResponse.status,responseBody,responseBodySha256:digest(responseBody),unauthenticatedStatus:401,readyFileSha256:pin("coordinator-cancellation-ready.json"),beforeTraceFileSha256:pin("trace-target-before.http.json")});
  }else{await until(()=>store.getRun(runs[1].runId)!.status==="completed","Worker deadline did not preserve primary",10000);}
  await trace(2,isDeadline?"trace-peer-after-deadline.http.json":"trace-peer-after-cancel.http.json");assert.ok(!peerClosed && peerResponse && !peerResponse.destroyed);
  await receipt(drainedFile,15000);await receipt("native-recovered.json",90000);
  await trace(2,"trace-peer-before-release.http.json");assert.ok(!peerClosed && peerResponse && !peerResponse.destroyed);
  if(isRealPrimary)await until(()=>peerReleased,"Actual primary peer response deadline exceeded",spec.primaryTimeoutMs);
  else{peerReleased=true;save("peer-primary-released.json",{index:2,runId:runs[2].runId,releasedAt:now(),socketClosedBeforeRelease:peerClosed,socketOpenAtRelease:!peerResponse.destroyed,responseBytesWrittenBeforeRelease:0,
    recoveredFileSha256:pin("native-recovered.json"),heldFileSha256:pin("peer-primary-held.json")});resolvePeer!();}
  if(!isDeadline){const targetAssignment=before.decisionCallerAccounting.stages.find((s:any)=>s.stageId===targetStage.id).assignments[0];
  const targetIntent=coordinatorRows.find(r=>r.path.endsWith("/decision-shadow/intent") && JSON.parse(r.requestBody).assignmentId===targetAssignment.assignmentId);assert.ok(targetIntent);
  const targetLeasePath=targetIntent.path.slice(0,-"/decision-shadow/intent".length);
  await until(()=>coordinatorRows.some(r=>r.path===targetLeasePath+"/decision-shadow" && r.httpStatus===400) && coordinatorRows.some(r=>r.path===targetLeasePath+"/complete" && r.httpStatus===400),"Target late writes were not fenced",10000);}
  route(1,await trace(1,"trace-target.http.json"));
  await until(()=>store.getRun(runs[2].runId)!.status==="completed","Unaffected peer failed",isRealPrimary?spec.primaryTimeoutMs:30000);route(2,await trace(2,"trace-peer.http.json"));
  start(3);await until(()=>store.getRun(runs[3].runId)!.status==="completed","Recovery suffix failed",isRealPrimary?spec.primaryTimeoutMs:30000);route(3,await trace(3,"trace-suffix.http.json"));
  await stopWorker();assert.equal(worker.exitCode,0);assert.ok(!failure);const endAt=now();
  for(const version of isDeadline?[1,2]:[1]){
  const endpoint=coordinatorUrl+`/api/v1/processes/${processId}/decision-shadow-cohort?`+new URLSearchParams({processVersion:String(version),startAt,endAt});
  const unauthorized=await fetch(endpoint,{signal:AbortSignal.timeout(5000),redirect:"error"});assert.equal(unauthorized.status,401);await unauthorized.text();
  const response=await fetch(endpoint,{headers:{"x-agat-admin-token":adminToken},signal:AbortSignal.timeout(10000),redirect:"error"});assert.equal(response.status,200);
  const raw=await response.text();assert.ok(Buffer.byteLength(raw)<=16*1024*1024);fs.writeFileSync(path.join(directory,version===1?"cohort.http.json":"cohort-target.http.json"),raw,{flag:"wx",mode:0o600});
  if(isDeadline)journal("cohort-http.jsonl",{version,path:new URL(endpoint).pathname+new URL(endpoint).search,httpStatus:response.status,
    unauthenticatedStatus:unauthorized.status,bodySha256:digest(raw),capturedAt:now()});}
  save("workflow-driver.json",{status:"observed",failure:null,routes,primaryCalls:primaryRows.length,workerExitCode:worker.exitCode,ownedPids:[process.pid,worker.pid],
    nodeVersion:process.version,workerConcurrency:2,schedulerMode:store.getSetting("scheduler_mode"),globalMaxConcurrency:Number(store.getSetting("global_max_concurrency")),endAt,authenticatedStatus:200,unauthenticatedStatus:401});
}finally{primaryAbort.abort();if(!peerReleased){peerResponse?.destroy();resolvePeer?.();}await stopWorker();fs.closeSync(log);if(fs.existsSync(credential))fs.unlinkSync(credential);
  await close(coordinator);await close(primary);store.close();}
