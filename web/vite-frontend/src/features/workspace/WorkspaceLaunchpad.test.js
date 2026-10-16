import test from 'node:test';
import assert from 'node:assert/strict';
import {installWorkspaceLaunchpad} from './WorkspaceLaunchpad.js';
test('opening XRD twice creates distinct tabs instead of activating an existing singleton',()=>{
 const opened=new Map();let active;
 const controller={openToolTab(id,label,render){active=id;if(!opened.has(id))opened.set(id,{label,render});}};
 const launchpad=installWorkspaceLaunchpad({controller});
 launchpad.openXrd();const first=active;
 launchpad.openXrd();assert.notEqual(active,first);assert.equal(opened.size,2);
 assert.deepEqual([...opened.values()].map(x=>x.label),['XRD 1','XRD 2']);
});
