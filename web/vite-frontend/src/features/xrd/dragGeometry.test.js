import test from 'node:test';
import assert from 'node:assert/strict';
import {dragOffset} from './dragGeometry.js';
const viewport={width:1200,height:900};
test('centered floating window does not jump at drag start',()=>{
 assert.deepEqual(dragOffset({left:300,top:100,width:600,height:700},viewport,{x:0,y:0},{x:0,y:0},1),{x:0,y:0});
});
test('viewport movement converts through MC interface scale without applying canvas zoom twice',()=>{
 assert.deepEqual(dragOffset({left:100,top:80,width:660,height:700},viewport,{x:20,y:10},{x:80,y:40},.8),{x:120,y:60});
});
test('second drag continues from existing displacement',()=>{
 assert.deepEqual(dragOffset({left:400,top:180,width:600,height:600},viewport,{x:100,y:80},{x:-40,y:20},1),{x:60,y:100});
});
test('bounds account for the whole visible window',()=>{
 assert.deepEqual(dragOffset({left:300,top:100,width:600,height:700},viewport,{x:0,y:0},{x:1000,y:1000},1),{x:300,y:100});
});
test('oversized window retains reachable top left',()=>{
 assert.deepEqual(dragOffset({left:-100,top:-50,width:1400,height:1000},viewport,{x:0,y:0},{x:-100,y:-100},1),{x:100,y:50});
});
