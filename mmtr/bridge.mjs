/** Private stdio bridge for non-JavaScript hosts. No network listener. */
import { createInterface } from 'node:readline';
import MemorableMemory from './index.mjs';

const memory = new MemorableMemory(process.argv[2] || ':memory:', {
  sessionGapMs: Number(process.env.MMTR_SESSION_GAP_MS || 1800000),
});
const allowed = new Set(['remember', 'getMemory', 'deleteMemory', 'setCore', 'coreMemory', 'deleteCore',
  'promise', 'keep', 'relationship', 'touchSession', 'endSession', 'recall', 'consolidate', 'health']);
const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
  let request;
  try {
    if (Buffer.byteLength(line, 'utf8') > 1_000_000) throw new RangeError('request exceeds 1MB');
    request = JSON.parse(line);
    if (!allowed.has(request.method) || !Array.isArray(request.args)) throw new TypeError('unsupported bridge method');
    const result = memory[request.method](...request.args);
    process.stdout.write(JSON.stringify({ id: request.id, ok: true, result }) + '\n');
  } catch (error) {
    process.stdout.write(JSON.stringify({ id: request?.id, ok: false, error: error.message }) + '\n');
  }
}
memory.close();
