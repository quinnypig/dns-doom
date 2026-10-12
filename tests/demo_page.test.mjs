// Regression tests for the browser page script in dns32/demo.html.
// Runs the page's inline script against a tiny fake DOM; no DNS run needed.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const html=readFileSync(new URL('../dns32/demo.html',import.meta.url),'utf8');
const script=html.match(/<script>([\s\S]*)<\/script>/)[1];

function element(id){
  const classes=new Set(id==='frame'||id==='progress'||id==='error'||id==='controls'||id==='report'?['hidden']:[]);
  const el={id,textContent:'',value:0,disabled:false,style:{},attrs:{},
    classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c),
      toggle:(c,force)=>{(force===undefined?!classes.has(c):force)?classes.add(c):classes.delete(c)}},
    removeAttribute(name){delete el.attrs[name]}};
  Object.defineProperty(el,'src',{get:()=>el.attrs.src,set:v=>{el.attrs.src=v}});
  return el;
}

const settle=()=>new Promise(r=>setImmediate(r));

async function page(){
  const els={};
  const state={status:{phase:'idle'}};
  const context={document:{getElementById:id=>els[id]??=element(id)},
    addEventListener(){},setInterval(){},innerWidth:1200,innerHeight:800,devicePixelRatio:1,
    fetch:async()=>({ok:true,json:async()=>state.status}),console};
  vm.createContext(context);
  vm.runInContext(script,context);
  await settle(); // let the script's own first poll finish so the busy guard is clear
  const poll=async()=>{await vm.runInContext('poll()',context);await settle()};
  return {els,state,poll,async show(status){state.status=status;await poll()},
    hidden:id=>els[id].classList.contains('hidden')};
}

const view={width:96,height:48},engine={engine_before:{gametic:1},engine_after:{gametic:2}};
const complete=(checks,extra={})=>({phase:'complete',id:'run-1',program:'doom',finished:1,preview:false,
  report:{view,...engine,elapsed:146.3,instructions:1389327,dns_transactions:8206070,clock_cycles:507332,
    network_batches:5055888,backend:'DNS / stock bind',...checks},...extra});
const allTrue={reference_pixels_match:true,reference_registers_match:true,reference_game_state_match:true,reference_pc_match:true};

test('complete match requires every declared check, including PC',async()=>{
  const p=await page();await p.show(complete(allTrue));
  assert.equal(p.els.verification.textContent,'Match');
  assert.match(p.els.facts.textContent,/pixels match, registers match, game state match, PC match\./);
  assert.equal(p.hidden('report'),false);
});

test('missing checks are reported as partial, not as a match',async()=>{
  const p=await page();await p.show(complete({reference_pixels_match:true}));
  assert.equal(p.els.verification.textContent,'Partial: pixels');
  assert.match(p.els.facts.textContent,/pixels match, registers not checked, game state not checked, PC not checked\./);
  const q=await page();await q.show(complete({...allTrue,reference_pc_match:undefined}));
  assert.equal(q.els.verification.textContent,'Partial: pixels, registers, game state');
});

test('any false check is a mismatch and names the failing checks',async()=>{
  const p=await page();await p.show(complete({...allTrue,reference_registers_match:false,reference_pc_match:undefined}));
  assert.equal(p.els.verification.textContent,'Mismatch: registers');
  assert.match(p.els.facts.textContent,/registers do not match, game state match, PC not checked/);
});

test('no checks at all reads Not checked',async()=>{
  const p=await page();await p.show(complete({}));
  assert.equal(p.els.verification.textContent,'Not checked');
});

test('idle after a completed run clears the stale frame, metrics, report, and error',async()=>{
  const p=await page();
  await p.show({...complete(allTrue),preview:true,error:'old warning'});
  assert.equal(p.hidden('frame'),false);assert.ok(p.els.frame.src);
  assert.equal(p.els.instructions.textContent,'1,389,327');assert.equal(p.hidden('error'),false);
  await p.show({phase:'idle'});
  assert.equal(p.els.phase.textContent,'Idle');assert.equal(p.els.detail.textContent,'');
  assert.equal(p.hidden('frame'),true);assert.equal(p.els.frame.src,undefined);
  assert.equal(p.hidden('empty'),false);assert.equal(p.els.empty.textContent,'No frame yet.');
  for(const id of ['instructions','queries','elapsed','verification'])assert.equal(p.els[id].textContent,'—');
  assert.equal(p.hidden('report'),true);assert.equal(p.els.facts.textContent,'');
  assert.equal(p.hidden('error'),true);assert.equal(p.hidden('progress'),true);
});

test('idle after a running job clears progress and the live preview',async()=>{
  const p=await page();
  await p.show({phase:'running',id:'run-2',program:'doom',preview:true,progress:{instructions:412000,elapsed:61.2,dns_transactions:2400000}});
  assert.equal(p.hidden('progress'),false);assert.equal(p.els.progress.value,412000);assert.equal(p.hidden('frame'),false);
  await p.show({phase:'idle'});
  assert.equal(p.hidden('progress'),true);assert.equal(p.els.progress.value,0);
  assert.equal(p.hidden('frame'),true);assert.equal(p.els.instructions.textContent,'—');
});

test('a new run id replaces the previous result',async()=>{
  const p=await page();await p.show(complete(allTrue));
  await p.show({phase:'starting',id:'run-9',program:'doom'});
  assert.equal(p.els.phase.textContent,'Starting');assert.equal(p.hidden('report'),true);
  assert.equal(p.els.verification.textContent,'—');assert.equal(p.els.empty.textContent,'Loading the checkpoint into DNS.');
});

test('visible labels use the agreed wording',()=>{
  assert.match(html,/<dt>DNS transactions<\/dt>/);
  assert.doesNotMatch(html,/DNS queries/);
  assert.match(html,/<p class="sub">A computer whose arithmetic and memory are DNS records\.<\/p>/);
});
