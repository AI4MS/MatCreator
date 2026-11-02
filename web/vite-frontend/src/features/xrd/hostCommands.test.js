import test from 'node:test';
import assert from 'node:assert/strict';
import {createMultiphaseReview,archiveDownloadUrl} from './hostCommands.js';
import {localAccountUrl} from '../auth/localAccessFetch.js';
test('vendor review callback serializes run ID and selected combinations as an object',async()=>{
 let request;
 const review=createMultiphaseReview(async(action,args)=>{request=JSON.parse(JSON.stringify({action,arguments:args}));return {status:'running'};});
 const result=await review('run-existing',[['phase-a','phase-b'],['phase-c']]);
 assert.deepEqual(request,{action:'multiphase_review',arguments:{run_id:'run-existing',combinations:[['phase-a','phase-b'],['phase-c']]}});
 assert.equal(result.status,'running');
});

test('native XRD archives keep their originating account while embedded archives remain local',()=>{
 const bind=url=>localAccountUrl(url,'account-a','http://mc-instance.localhost:15173/');
 const url=archiveDownloadUrl('/api/xrd/workspaces/a/archive',bind);
 assert.equal(new URL(url,'http://localhost').searchParams.get('expected_account_id'),'account-a');
 assert.equal(archiveDownloadUrl('AAAA',bind),'data:application/zip;base64,AAAA');
});
