import MemorableMemory, { DAY } from '../index.mjs';

let now = Date.UTC(2026, 9, 29);
const m = new MemorableMemory(':memory:', { clock: () => now });
m.setCore('user', '他的名字是小雨');
m.promise({
  text: '明年生日来我家一起过。',
  gist: '生日的约定', entity: '小雨',
  at: now - 365 * DAY, due: now + 100 * DAY, anchors: ['02-06'],
});
const id = m.remember({
  fact: '雨天一起包饺子，聊到十一点。', gist: '一起包饺子很合拍', affect: '安心，亲切', entities: ['小雨'], themes: ['做饭'],
});
console.log('提起包饺子：\n' + m.recall('包饺子'));
now += 60 * DAY;
m.consolidate();
console.log('\n两个月后：\n' + m.recall('包饺子'));
console.log('\n事实是否已压缩：', m.getMemory(id).channels.find(c => c.channel === 'fact').content === '');
m.close();
