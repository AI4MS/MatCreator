import test from 'node:test';
import assert from 'node:assert/strict';
import {createMultiphaseReview} from './hostCommands.js';
test('vendor review callback serializes run ID and selected combinations as an object',async()=>{
 let request;
 const review=createMultiphaseReview(async(action,args)=>{request=JSON.parse(JSON.stringify({action,arguments:args}));return {status:'running'};});
 const result=await review('run-existing',[['phase-a','phase-b'],['phase-c']]);
 assert.deepEqual(request,{action:'multiphase_review',arguments:{run_id:'run-existing',combinations:[['phase-a','phase-b'],['phase-c']]}});
 assert.equal(result.status,'running');
});
