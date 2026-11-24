const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context={window:{}};vm.createContext(context);
vm.runInContext(fs.readFileSync('web/static/research-navigation.js','utf8'),context);
const steps=[{stage:'topic_discovery',nodes:['trend_fetch','topic_recommend']},{stage:'topic_discovery',nodes:['topic_select']},{stage:'research',nodes:['topic_confirm']},{stage:'research',nodes:['paper_fetch','paper_ingest']},{stage:'research',nodes:['cross_validate','research_gap']},{stage:'ideation',nodes:['brainstorm','idea_discuss']},{stage:'design',nodes:['method_formalize','method_review']},{stage:'experiment',nodes:['experiment_run']},{stage:'writing',nodes:['revise']}];
const progress=context.window.SRA_NAVIGATION.executionProgress;
for (const current of ['cross_validate',{node_id:'cross_validate'}]){
    const state=progress({status:'running',current_node:current,current_stage:'research'},steps);
    assert.equal(state.stepIndex,4);
}
const failed=progress({status:'failed',current_stage:'ideation',stage_statuses:{research:'done',ideation:'blocked'},node_history:[{node_id:'cross_validate',status:'success'},{node_id:'brainstorm',status:'failed'}]},steps);
assert.equal(failed.stepIndex,5);assert.equal(failed.states[5],'failed');assert.equal(failed.states[6],'pending');
const pending=progress({status:'experiment_pending',current_stage:'writing',stage_statuses:{experiment:'done'},node_history:[{node_id:'experiment_run',status:'success'}]},steps);
assert.equal(pending.stepIndex,7);assert.equal(pending.states[7],'active');assert.equal(pending.states[8],'pending');
assert.equal(progress({status:'completed'},steps).states.includes('active'),false);
assert.equal(progress({status:'created'},steps).stepIndex,-1);
console.log(JSON.stringify({passed:7,checks:['node-object','legacy-node-string','failure-not-completion','waiting-experiment-not-writing','completed-no-current','created-no-current']}));
