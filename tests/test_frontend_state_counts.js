const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/app.js'), 'utf8');
const nodes = {};
const context = vm.createContext({
    isDateOverdue: () => false,
    el: id => nodes[id] ||= {style: {}},
    currentFindings: [{status:'Finalizado'}, {status:'En proceso'}],
    currentActionPlans: [{status:'Finalizado'}, {status:'Completada'}, {status:'En proceso'}],
});
for (const name of ['getRowEffectiveStatus', 'isFinalized', 'isOpenProposalWithoutPlan', 'updateSidebarMetrics']) {
    const start = source.indexOf(`function ${name}(`);
    const end = source.indexOf('\n}\n', start) + 3;
    vm.runInContext(source.slice(start, end), context);
}
for (const status of ['Finalizado','Completada','Cerrado','implementado']) {
    assert.equal(context.isFinalized({status}), true);
    assert.equal(context.isOpenProposalWithoutPlan({status,action_plans_count:0}), false);
}
assert.equal(context.isOpenProposalWithoutPlan({status:'En proceso',action_plans_count:0}), true);
assert.equal(context.isOpenProposalWithoutPlan({status:'En proceso',action_plans:[{}]}), false);
context.updateSidebarMetrics();
assert.equal(nodes.sidebarOpenCount.textContent, 1);
assert.equal(nodes.sidebarPct.textContent, '67%');
console.log('Frontend: completed states, open proposals without plans, sidebar counts OK');
