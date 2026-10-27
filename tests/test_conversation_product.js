/* Run with: node tests/test_conversation_product.js. No browser or dependencies. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../web/static/conversation-home.js'), 'utf8');
const documentState = {activeElement: null};

// A small DOM stand-in exercises the production controllers and their network
// boundaries; live browser acceptance covers layout and native dialog behavior.
class Element {
    constructor(tag, cls = '', text = '') {
        this.tag = tag; this.className = cls; this._text = text; this.children = [];
        this.dataset = {}; this.events = {}; this.isConnected = true;
        this.classList = {toggle() {}};
    }
    append(...children) { for(const child of children)child.parentElement = this; this.children.push(...children); }
    replaceChildren(...children) { this.children = children; this._text = ''; }
    setAttribute() {}
    addEventListener(name, callback) { this.events[name] = callback; }
    querySelector(selector) { return this.all().find(n => ['details', 'summary'].includes(selector) ? n.tag === selector : selector === '[data-teaching="directory"]' && n.dataset.teaching === 'directory') || null; }
    contains(element) { return this === element || this.all().includes(element); }
    all() { return this.children.flatMap(n => [n, ...n.all()]); }
    get textContent() { return this._text + this.children.map(n => n.textContent).join('\n'); }
    set textContent(text) { this._text = text; this.children = []; }
    focus() { this.focused = true; documentState.activeElement = this; }
    showModal() { this.open = true; }
    close() { this.open = false; }
    remove() { this.isConnected = false; if(this.parentElement)this.parentElement.children = this.parentElement.children.filter(n => n !== this); }
    dispatchEvent(event) { this.events[event.type]?.(event); }
}
const E = (tag, cls, text) => new Element(tag, cls, text);
const button = (text, callback, cls) => { const n = E('button', cls, text); n.onclick = callback; return n; };
const notices = [], calls = [], closedPanels = [];
let response, failure;
const context = vm.createContext({
    E, button, URLSearchParams, Event, document: documentState,
    current: v => v.active !== false,
    request: async (url, options = {}) => { calls.push({url, options}); if(failure)throw failure; return response; },
    notice: (v, text) => notices.push(text),
    panel: (...args) => { closedPanels.push(args); args[0].r.panel.hidden = !args[1]; },
    saveDraft: v => { v.saved = v.r.input.value; v.savedPlanRequest = v.planRequest ? {...v.planRequest} : null; },
    openMaterial() {},
    window: {dispatchEvent() {}}, CustomEvent: class {},
    Option: class extends Element {constructor(text, value) {super('option', '', text); this.value = value;}},
    g: {cap: {research_plan_templates: true}}, paintMaterials() {}, paintMessages() {}, loadResearchRoute: async () => {},
});
const functions = [
    source.slice(source.indexOf('    const ENTRY_PATHS='), source.indexOf('    function paintResearchRoute(')),
    source.slice(source.indexOf('    function runtimeBlocked('), source.indexOf('    async function capabilities(')),
    source.slice(source.indexOf('    function planQuality('), source.indexOf('    function planReview(')),
    source.slice(source.indexOf('    function prepareSearchInput('), source.indexOf('    async function chooseReply(')),
    source.slice(source.indexOf('    async function refreshMaterials('), source.indexOf('    async function downloads(')),
    source.slice(source.indexOf('    async function firstSend('), source.indexOf('    async function retry(')),
    source.slice(source.indexOf('    async function chooseReply('), source.indexOf('    async function runAction(')),
    source.slice(source.indexOf('    function taskPapers('), source.indexOf('    function registerTask(')),
    source.slice(source.indexOf('    async function pollTask('), source.indexOf('    function paintTasks(')),
].join('\n');
vm.runInContext(functions + '\nthis.controllers={runtimeBlocked,allowGeneration,loadRuntime,planQuality,preparePlanInput,prepareSearchInput,refreshMaterials,firstSend,chooseReply,selectEntry,journeyNext,runJourneyNext,taskPapers,taskResult,prepareTaskDiscussion,pollTask};', context);
// paintHeader belongs to the full app; the tested refresh must call it without
// navigating, generating, or depending on unrelated project-list DOM.
context.paintHeader = () => {};
const c = context.controllers;
const view = () => ({
    pid: 'p1', cid: 'c1', material: {}, reads: new AbortController(),
    root: E('section'), r: {runtime: E('section'), panel: Object.assign(E('aside'), {hidden:true}), input: Object.assign(E('textarea'), {value: '', maxLength: 6000}), planPending: E('p'), planTools: E('details'), planMethod: {}, planQuestion: {}},
});
const find = (root, text) => root.all().find(n => n.tag === 'button' && n.textContent === text);
const flush = () => new Promise(resolve => setImmediate(resolve));

(async () => {
    const entryView=()=>{const v=view();v.pid=null;v.cid=null;v.entryId=null;
        Object.assign(v.r,{entryGuide:E('section'),entryTitle:E('strong'),entryOutcome:E('p'),entryNeed:E('p'),entryUpload:E('button'),mode:{value:'direct'},stage:{value:'orientation'},goal:{value:''}});
        v.root.querySelectorAll=()=>[0,1,2,3].map(id=>Object.assign(E('button'),{dataset:{entry:String(id)}}));return v;};
    const beforeEntries=calls.length;
    for(const [id,mode,stage,step] of [[0,'beginner','orientation','conditions'],[1,'direct','literature','exploration'],[2,'direct','question','question'],[3,'direct','literature','reading']]){
        const v=entryView();await c.selectEntry(v,id);
        assert.equal(v.r.mode.value,mode);assert.equal(v.r.stage.value,stage);assert.equal(v.discussionStep,step);
        assert.equal(v.r.entryGuide.hidden,false);assert.ok(v.r.entryOutcome.textContent.includes('这次可以得到'));
        assert.equal(v.r.entryUpload.hidden,id!==3);
    }
    assert.equal(calls.length,beforeEntries,'choosing an entry never starts a project or calls a model');
    const draftEntry=entryView();await c.selectEntry(draftEntry,3);await c.selectEntry(draftEntry,0);
    assert.ok(!draftEntry.r.input.value.includes('我手上有一篇论文'),'an unchanged starter is replaced when choosing another path');
    draftEntry.r.input.value='用户自己写下的重要想法';
    const canceled=c.selectEntry(draftEntry,2);await flush();
    find(draftEntry.root,'保留草稿，暂不追加').onclick();await canceled;
    assert.equal(draftEntry.r.input.value,'用户自己写下的重要想法');assert.equal(draftEntry.entryId,0);assert.equal(draftEntry.r.stage.value,'orientation');
    const accepted=c.selectEntry(draftEntry,2);await flush();find(draftEntry.root,'保留并追加').onclick();await accepted;
    assert.ok(draftEntry.r.input.value.startsWith('用户自己写下的重要想法'));assert.equal(draftEntry.entryId,2);assert.equal(draftEntry.r.stage.value,'question');
    assert.ok(draftEntry.r.input.value.endsWith('本次不生成或修订方案。'));
    draftEntry.r.input.value='请立即生成实验研究方案';draftEntry.planRequest={method:'experimental',question_id:'q1'};
    const discussEntry=c.selectEntry(draftEntry,3);await flush();find(draftEntry.root,'保留并追加').onclick();await discussEntry;
    assert.equal(draftEntry.planRequest,null);assert.ok(draftEntry.r.input.value.endsWith('本次不生成或修订方案。'));
    assert.ok(draftEntry.r.input.value.includes('暂不检索或搜索。'));
    assert.equal(calls.length,beforeEntries,'appending an entry only prepares an editable draft');
    const routeView=view(),design={id:'design',action:'plan',blocked_reason:'先确认问题'};
    routeView.material={workflow:{flow:{}}};
    assert.equal(c.journeyNext(routeView,design).action,'question','a missing question offers recovery instead of a disabled end');
    routeView.material.workflow.flow.plan={item_id:'plan1'};
    assert.equal(c.journeyNext(routeView,design).action,'plan','saved plans are opened, never regenerated by default');
    routeView.material.workflow.flow.question={item_id:'question1'};
    routeView.suggestions=[{kind:'plan',suggestion_id:'revision1',plan:{question_id:'question1'},basis_changed:false,material_fingerprint:'current'}];
    assert.equal(c.journeyNext(routeView,design).id,'revision1','a current question revision must be reviewed before reopening the older saved plan');
    routeView.suggestions[0].plan.question_id='other-question';
    assert.equal(c.journeyNext(routeView,design).id,'plan1','another question draft cannot displace the current plan');
    routeView.suggestions[0].plan.question_id='question1';routeView.suggestions[0].basis_changed=true;
    assert.equal(c.journeyNext(routeView,design).id,'plan1','stale drafts cannot displace the saved plan');
    routeView.suggestions[0].basis_changed=false;routeView.suggestions[0].applied_item_id='plan1';
    assert.equal(c.journeyNext(routeView,design).id,'plan1','adopted drafts resolve to the saved plan');
    routeView.suggestions=[];
    routeView.material.workflow.flow={question:{item_id:'question1'}};
    assert.equal(c.journeyNext(routeView,{id:'question'}).action,'design');
    routeView.suggestions=[{kind:'question',suggestion_id:'candidate1',basis_changed:false,material_fingerprint:'current'}];
    assert.equal(c.journeyNext(routeView,design).action,'proposal','a real question candidate can be opened before adoption');
    routeView.researchRoute={step:'paused'};
    assert.equal(c.journeyNext(routeView,design).action,'focus','paused research cannot be resumed by rendering a recommendation');
    context.chooseResearchStep=async(v,id)=>{v.discussionStep=id;};
    const recovery=view();recovery.discussionStep='design';recovery.r.input.value='我的研究问题是：相同数据和预算下，两种声音分类方法的误差怎样比较？';
    let recovering=c.runJourneyNext(recovery,{action:'question'},design);await flush();find(recovery.root,'保留草稿，暂不追加').onclick();await recovering;
    assert.equal(recovery.discussionStep,'design','cancelled recovery keeps the selected task');
    recovering=c.runJourneyNext(recovery,{action:'question'},design);await flush();find(recovery.root,'保留并追加').onclick();await recovering;
    assert.equal(recovery.discussionStep,'question','confirmed question recovery must discuss the question instead of staying in blocked design');
    assert.match(recovery.r.input.value,/^我的研究问题是/);assert.match(recovery.r.input.value,/本次不生成或修订方案。$/);
    const resultView=view();resultView.material={papers:[{paper_id:'source-id-1',title:'可阅读的论文'},{paper_id:'excluded-id',title:'被筛除的资料'}]};
    const resultTask={task_id:'task1',status:'completed',current_version:1,included_count:1,saved_paper_ids:['source-id-1','excluded-id'],excluded:[{paper_id:'excluded-id'}],report:{mode:'model',methods_relations:'只依据 source-id-1 的摘要比较方法。',representative:[{paper_id:'source-id-1',point:'方法条件仍待核实。'}],coverage:{included_count:1,abstract_count:1,metadata_only_count:0}}};
    assert.equal(c.taskResult(resultView,{status:'running'}),null);
    const result=c.taskResult(resultView,resultTask);
    assert.match(result.textContent,/本轮检索摘要 · v1/);assert.match(result.textContent,/《可阅读的论文》/);assert.match(result.textContent,/摘要 1 条/);assert.match(result.textContent,/未核对全文/);
    assert.doesNotMatch(result.textContent,/source-id-1|被筛除的资料/);
    assert.equal(c.taskPapers(resultView,{...resultTask,report:null}).length,1,'saved but excluded papers are never offered as the reading batch');
    const partial=c.taskResult(resultView,{...resultTask,status:'partial',synthesis_note:'rule fallback',report:{...resultTask.report,mode:'rules'}});
    assert.match(partial.textContent,/部分结果/);assert.match(partial.textContent,/没有完成方法或结论分析/);assert.match(partial.textContent,/规则回退/);
    const zero=c.taskResult(resultView,{...resultTask,included_count:0,candidates_count:3,report:{...resultTask.report,coverage:{included_count:0,abstract_count:0,metadata_only_count:0},representative:[],methods_relations:''}});
    assert.match(zero.textContent,/筛选后没有纳入/);assert.match(zero.textContent,/不代表领域没有/);assert.equal(find(zero,'阅读这批资料'),undefined);
    assert.match(c.taskResult(resultView,{status:'failed',report:null,saved_paper_ids:[]}).textContent,/尚无成功保存的内容报告/);
    assert.equal(find(c.taskResult(resultView,{...resultTask,basis_changed:true}),'阅读这批资料'),undefined,'known changed basis must be reviewed before continuing');
    assert.match(c.taskResult(resultView,{...resultTask,report:{...resultTask.report,methods_relations:'原始长摘要'.repeat(200)}}).textContent,/展开完整摘要/);
    const beforeReport=calls.length;resultView.r.input.value='先保留我的疑问';resultView.planRequest={question_id:'old'};resultView.paperIds=['old-paper'];resultView.discussionStep='conditions';
    let reading=c.prepareTaskDiscussion(resultView,resultTask,'reading');await flush();find(resultView.root,'保留草稿，暂不追加').onclick();await reading;
    assert.equal(resultView.r.input.value,'先保留我的疑问');assert.deepEqual(resultView.paperIds,['old-paper']);assert.equal(resultView.discussionStep,'conditions');assert.ok(resultView.planRequest);
    reading=c.prepareTaskDiscussion(resultView,resultTask,'reading');await flush();find(resultView.root,'保留并追加').onclick();await reading;
    assert.equal(resultView.discussionStep,'reading');assert.deepEqual(Array.from(resultView.paperIds),['source-id-1']);assert.equal(resultView.planRequest,null);assert.match(resultView.r.input.value,/^先保留我的疑问/);assert.match(resultView.r.input.value,/本次不检索或搜索/);
    assert.equal(calls.length,beforeReport,'result continuation only prepares editable text, never sends or searches');
    let v = view();
    v.runtime = {mode: 'rules', status: 'rules_mode', checks: [{status: 'missing_credentials'}]};
    assert.equal(await c.allowGeneration(v), true, 'missing model credentials cannot block rule guidance');
    v.runtime = {mode: 'model', status: 'needs_configuration', checks: [{task_type: 'literature_search', status: 'missing_credentials'}]};
    assert.equal(await c.allowGeneration(v), false);
    assert.match(notices.at(-1), /草稿已保留/);
    assert.equal(calls.length, 0, 'a known configuration failure must not send any request');
    v.runtime.checks.push({task_type: 'research_synthesize', status: 'configured_unverified'});
    assert.equal(await c.allowGeneration(v), true, 'a partial configuration failure cannot block configured functionality');
    v.runtime.checks.pop();
    v.material.teaching = {};
    assert.equal(await c.allowGeneration(v), true, 'teaching is independent of model configuration');
    v.material = {}; v.runtime = {mode: 'model', status: 'configured_unverified'};
    assert.equal(await c.allowGeneration(v), true, 'configured means permitted to try, never verified success');

    v = view(); response = {schema_version: 'runtime-status.v1', mode: 'rules', status: 'rules_mode', title: '离线规则指导', message: '未调用模型', actions: [], checks: []};
    await c.loadRuntime(v);
    assert.equal(v.runtime.status, 'rules_mode');
    assert.equal(calls.at(-1).url, '/api/runtime-status');
    assert.equal(calls.at(-1).options.method, undefined, 'status refresh is GET only');
    assert.equal(v.r.input.value, '');
    find(v.r.runtime, '重新读取配置').focus();
    await c.loadRuntime(v);
    assert.equal(documentState.activeElement.tag, 'summary', 'refresh keeps keyboard focus in the replacement control');

    const selection = view(); selection.cid = null; selection.planRequest = {question_id:'q1'};
    selection.r.planQuestion = Object.assign(E('select'), {value:'',add(option) {this.append(option);}});
    selection.r.preparePlan = {}; selection.r.planEligibility = {};
    response = {workspace:{revision:1},papers:[],workflow:{flow:{question:{item_id:'q2'},questions:[{item_id:'q1',title:'问题1'},{item_id:'q2',title:'问题2'}]}}};
    await c.refreshMaterials(selection);
    assert.equal(selection.r.planQuestion.value, 'q1', 'restored request and displayed question must agree');
    selection.r.planQuestion.value = 'q2'; selection.r.planQuestion.onchange();
    assert.equal(selection.planRequest.question_id, 'q2', 'changing the visible question updates the prepared request');
    assert.equal(selection.savedPlanRequest.question_id, 'q2', 'the chosen question survives the next reload');
    selection.writable = true; selection.r.planQuestion.value = ''; selection.r.input.value = '保留生成草稿';
    const beforeInvalidSelection = calls.length;
    await c.firstSend(selection, selection.r.input.value);
    assert.equal(calls.length, beforeInvalidSelection, 'an empty plan question cannot fall back to another project focus');
    assert.equal(selection.planRequest.question_id, 'q2');
    assert.equal(selection.r.planQuestion.focused, true);
    selection.r.planQuestion.value = 'deleted-question';
    await c.firstSend(selection, selection.r.input.value);
    assert.equal(calls.length, beforeInvalidSelection, 'a missing restored question cannot submit a plan');
    selection.r.planQuestion.value = 'q2'; selection.planRequest.question_id = null;
    context.allowGeneration = async () => false;
    await c.firstSend(selection, selection.r.input.value);
    assert.equal(selection.planRequest.question_id, 'q2', 'a valid visible question is sent explicitly instead of relying on focus fallback');
    context.allowGeneration = c.allowGeneration;

    const originalDraft = '请为所选研究问题生成一份研究方案，具体要求：';
    v.r.input.value = originalDraft; v.planRequest = {question_id: 'q1'};
    const originalRequest = v.planRequest;
    let preparation = c.preparePlanInput(v, '核对引用', null, true);
    find(v.root, '保留草稿，暂不追加').onclick();
    assert.equal(await preparation, false);
    assert.equal(v.r.input.value, originalDraft);
    assert.equal(v.planRequest, originalRequest);
    preparation = c.preparePlanInput(v, '核对引用', null, true);
    v.root.children.at(-1).all().find(n => n.tag === 'button' && n.textContent === '保留并追加').onclick();
    assert.equal(await preparation, true);
    assert.equal(v.r.input.value, originalDraft + '\n\n核对引用\n\n本次不生成或修订方案。');
    assert.equal(v.planRequest, null, 'explicitly confirmed discussion clears generation metadata');
    assert.equal(v.saved, v.r.input.value);
    assert.match(v.r.input.value, /本次不生成或修订方案。$/, 'ordinary review must explicitly override any old generation wording');
    const discussionDraft = v.r.input.value;
    assert.equal(await c.preparePlanInput(v, '生成新的方案', {question_id:'q2'}), false, 'a conflicting no-generation instruction cannot be silently ignored');
    assert.equal(v.r.input.value, discussionDraft);
    assert.equal(v.planRequest, null);
    assert.match(notices.at(-1), /请先修改这段约束/);
    v.r.input.maxLength = v.r.input.value.length;
    const saved = v.r.input.value;
    preparation = c.preparePlanInput(v, '更多要求', null, true);
    v.root.children.at(-1).all().find(n => n.tag === 'button' && n.textContent === '保留并追加').onclick();
    assert.equal(await preparation, false);
    assert.equal(v.r.input.value, saved, 'oversized preparation cannot replace an existing draft');
    assert.equal(v.planRequest, null);
    const freshPlan = view();
    assert.equal(await c.preparePlanInput(freshPlan, '生成方案', {question_id:'q2'}), true);
    assert.equal(freshPlan.planRequest.question_id, 'q2');
    assert.doesNotMatch(freshPlan.r.input.value, /不生成/);
    const search = view(), searchText = '请帮我检索硫化物固态电解质的代表性论文。';
    search.r.input.value = originalDraft; search.planRequest = {question_id:'q1'};
    assert.equal(await c.prepareSearchInput(search, searchText), false);
    assert.equal(search.r.input.value, originalDraft, 'search preparation never mixes the old draft into the search topic');
    assert.equal(search.planRequest.question_id, 'q1');
    search.r.input.value = '';
    preparation = c.prepareSearchInput(search, searchText);
    find(search.root, '保留并追加').onclick();
    assert.equal(await preparation, true);
    assert.equal(search.r.input.value, searchText, 'an empty search composer receives the exact search request');
    assert.equal(search.planRequest, null, 'search preparation clears old generation metadata only after confirmation');
    const recommended = view(), sentRecommendations = [];
    recommended.r.input.value = originalDraft; recommended.planRequest = {question_id:'q1'};
    const preparedRecommendation = recommended.planRequest, beforeRecommendation = calls.length;
    context.firstSend = async (view, text) => {sentRecommendations.push(text);};
    await c.chooseReply(recommended, null, {content:'有没有更简单的方法可以回答同一个研究问题？'});
    assert.equal(sentRecommendations.length, 0, 'a recommendation cannot silently inherit an unrelated prepared plan');
    assert.equal(calls.length, beforeRecommendation);
    assert.equal(recommended.r.input.value, originalDraft);
    assert.equal(recommended.planRequest, preparedRecommendation);
    assert.match(notices.at(-1), /先完成或取消原方案请求/);
    await c.chooseReply(view(), null, {content:'普通讨论推荐'});
    assert.deepEqual(sentRecommendations, ['普通讨论推荐'], 'ordinary recommendations still send normally');
    context.firstSend = c.firstSend;

    const leaving = view(); leaving.r.input.value = '切换前的草稿';
    preparation = c.preparePlanInput(leaving, '补充要求', null, true);
    assert.equal(await c.preparePlanInput(leaving, '重复点击', null), false, 'only one confirmation is allowed at a time');
    leaving.active = false; leaving.reads.abort();
    assert.equal(await preparation, false, 'navigation cancels pending preparation');
    assert.equal(leaving.r.input.value, '切换前的草稿');
    assert.equal(leaving.preparingPlan, false);
    assert.equal(leaving.root.children.length, 0);

    v = view();
    response = {status: 'needs_attention', label: '有待补充或复核项', checks: [
        {code: 'structure', label: '方案结构', status: 'pass', detail: '已有结构化方案'},
        {code: 'conditions', label: '研究条件', status: 'attention', detail: '请确认设备'},
    ], evidence: [{statement: '材料性质陈述', title: '来源', paper_id: 'paper1', location: '摘要（未核对全文）', quote: '1 mS/cm at 25 °C', matched: true}],
    condition_details: [{field: 'resources', label: '资源／设备', value: '尚未提供仪器', basis: 'unverified'}], unconfirmed: ['确认温度'], review_questions: ['引文是否支持陈述？'], notice: '不证明科学正确性',
    material_review: [{statement: '材料性质陈述', source_matched: true, quantities: ['1 mS/cm'], temperatures: ['25 °C'], condition_mentions: [], evidence_type: 'experimental', review_points: ['核对测量条件']}], material_notice: '仅为原文线索'};
    const review = E('details'); review.open = true;
    const quality = c.planQuality(v, {item_id: 'plan1', title: '待审方案'}, false, review);
    review.events.toggle(); await flush();
    assert.match(quality.textContent, /优先核对 1 项/);
    assert.match(quality.textContent, /尚未由你确认/);
    assert.match(quality.textContent, /1 mS\/cm/);
    assert.match(quality.textContent, /摘要（未核对全文）/);
    const beforeReopen = calls.length;
    response = {...response, evidence: response.evidence.map(entry => ({...entry, matched:false, location:'未匹配当前摘要或正文片段'}))};
    review.open = false; review.events.toggle(); review.open = true; review.events.toggle(); await flush();
    assert.equal(calls.length, beforeReopen + 1, 'reopening a saved review must recheck current sources');
    assert.match(quality.textContent, /未匹配当前摘要或正文片段/);
    const beforeDiscussion = calls.length;
    v.r.panel.hidden = false;
    v.r.input.value = '保留我的草稿';
    preparation = find(quality, '讨论这一项').onclick();
    v.root.children.at(-1).all().find(n => n.tag === 'button' && n.textContent === '保留草稿，暂不追加').onclick();
    await preparation;
    assert.equal(v.r.input.value, '保留我的草稿');
    assert.equal(closedPanels.length, 0, 'cancelled preparation must keep the reader open');
    preparation = find(quality, '讨论这一项').onclick();
    v.root.children.at(-1).all().find(n => n.tag === 'button' && n.textContent === '保留并追加').onclick();
    await preparation;
    assert.match(v.r.input.value, /^保留我的草稿\n\n请和我核对方案“待审方案”/);
    assert.equal(calls.length, beforeDiscussion, 'preparing a discussion cannot send or generate');
    assert.equal(closedPanels.length, 1);
    let completeCheck;
    const beforeConcurrentCheck = calls.length, deferredReview = E('details'); deferredReview.open = true;
    response = new Promise(resolve => {completeCheck = resolve;});
    const detached = c.planQuality(v, {item_id:'detached',title:'旧阅读'}, false, deferredReview);
    deferredReview.events.toggle(); deferredReview.open = false; deferredReview.events.toggle(); deferredReview.open = true; deferredReview.events.toggle();
    assert.equal(calls.length, beforeConcurrentCheck + 1, 'reopening during a pending check cannot duplicate the GET');
    detached.isConnected = false;
    completeCheck({status:'ready_for_discussion',label:'should-not-render'}); await flush();
    assert.doesNotMatch(detached.textContent, /should-not-render/, 'a late response cannot update detached review DOM');
    failure = new Error('offline');
    await find(quality, '重新核对').onclick();
    assert.match(quality.textContent, /保留上次检查/);
    assert.match(quality.textContent, /1 mS\/cm/, 'failed refresh retains the previous report');
    assert.equal(find(quality, '重新核对').disabled, false, 'retry remains actionable after failure');
    assert.ok(calls.every(call => call.options.method === undefined), 'all review and readiness operations must stay read-only');
    context.path=(v,suffix)=>'/saved-conversation'+suffix;context.paintTasks=()=>{};context.newMessages=async()=>{};context.refreshMaterials=async()=>{};
    const restored=view();restored.tasks={t1:{task_id:'t1',status:'completed'}};
    failure=null;response={task_id:'t1',status:'completed',report:{mode:'rules',representative:[]}};
    const beforeRestore=calls.length;await c.pollTask(restored,'t1');await c.pollTask(restored,'t1');
    assert.equal(calls.length,beforeRestore+1,'restoring a terminal task reads its exact saved report once');
    assert.equal(restored.tasks.t1.report.mode,'rules');assert.ok(restored.tasks.t1._settled);
    restored.tasks.t2={task_id:'t2',status:'partial'};failure=new Error('read unavailable');await c.pollTask(restored,'t2');
    assert.match(restored.tasks.t2._reportError,/不会重跑检索/);assert.equal(restored.tasks.t2._readingReport,false);
    failure=null;response={task_id:'t2',status:'partial',report:{mode:'model',representative:[]}};await c.pollTask(restored,'t2');
    assert.equal(restored.tasks.t2._reportError,null);assert.equal(restored.tasks.t2.report.mode,'model');
    assert.ok(calls.slice(beforeRestore).every(call=>call.options.method===undefined&&/research-tasks\/t[12]$/.test(call.url)),'summary restore and retry never call a search or generation endpoint');
    console.log('Conversation product behavior checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
