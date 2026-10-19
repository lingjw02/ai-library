// Uses the installed Gemini CLI MCP client implementation, not a mock transport.
// This is a runtime integration probe; it does not exercise an LLM prompt turn.
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const [corePath, settingsPath, workspace, reportPath, clientVersion] = process.argv.slice(2);
const checkHandoff = process.argv.slice(7).includes('--handoff');
const {McpClient} = await import(pathToFileURL(corePath));
const settings = JSON.parse(await fs.readFile(settingsPath, 'utf8'));
const server = settings.mcpServers['ai-memory-library'];
const fixtureContext = {getDirectories: () => [workspace], onDirectoriesChanged: () => () => {}};
const cliContext = {isTrustedFolder: () => true, emitMcpDiagnostic: () => {}};
const client = new McpClient('ai-memory-library', server, fixtureContext, cliContext, false, clientVersion);
const embeddingServer = settings.mcpServers['ai-library-embedding'];
const embedding = embeddingServer ? new McpClient('ai-library-embedding', embeddingServer, fixtureContext, cliContext, false, clientVersion) : null;
const outcomes = {};
const decoded = result => {
  if (result.isError) throw new Error(JSON.stringify(result));
  return JSON.parse(result.content[0].text);
};
try {
  await client.connect();
  const tools = await client.client.listTools();
  outcomes.tool_count = tools.tools.length;
  const notes = decoded(await client.client.callTool({name: 'list_user_notes', arguments: {}}));
  const note = notes.find(n => n.title === 'C2 actual Codex client');
  if (!note) throw new Error('Gemini runtime did not see the note created by Codex');
  outcomes.shared_codex_note_read = true;
  const submitted = decoded(await client.client.callTool({name: 'submit_candidate_claim', arguments: {
    source_id: note.id, source_revision: note.revision, source_content_hash: note.content_hash,
    claim: 'isolated C2 reuse evidence',
  }}));
  const memory = decoded(await client.client.callTool({name: 'accept_candidate', arguments: {candidate_id: submitted.id}}));
  if (memory.authority !== 'INFERRED' || memory.maturity !== 'L2') throw new Error('Unexpected authority');
  outcomes.memory_created = memory.id;
  const denied = await client.client.callTool({name: 'list_user_notes', arguments: {scope: 'private'}});
  if (!denied.isError) throw new Error('Scope boundary failed');
  outcomes.cross_scope_rejected = true;
  if (checkHandoff) {
    const handoffs = decoded(await client.client.callTool({name: 'list_work_handoffs', arguments: {target_system: 'gemini-cli'}}));
    const first = handoffs.items.find(item => item.payload.title === 'C2 actual client work exchange');
    if (!first || first.created_by !== 'c2-codex' || first.revision !== 1) throw new Error('Missing actual Codex handoff');
    const read = decoded(await client.client.callTool({name: 'get_work_handoff', arguments: {handoff_id: first.id}}));
    if (read.content_hash !== first.content_hash) throw new Error('Handoff revision changed unexpectedly');
    const receipt = decoded(await client.client.callTool({name: 'acknowledge_work_handoff', arguments: {
      handoff_id: first.id, revision: 1, content_hash: first.content_hash,
    }}));
    if (receipt.received_by !== 'c2-gemini' || receipt.meaning !== 'RECEIVED_ONLY') throw new Error('Wrong receipt identity or meaning');
    const revised = decoded(await client.client.callTool({name: 'revise_work_handoff', arguments: {
      handoff_id: first.id, expected_revision: 1, request_key: 'actual-gemini-resume-v1', payload: {
        ...read.payload, progress: [{description: 'Installed Gemini MCP module read and acknowledged the Codex handoff',
          state: 'TESTED', evidence: [reportPath]}], next_steps: ['Operator reviews evidence; actual model-driven continuation remains untested'],
      },
    }}));
    if (revised.revision !== 2 || revised.created_by !== 'c2-gemini') throw new Error('Gemini continuation was not persisted');
    const crossScope = await client.client.callTool({name: 'get_work_handoff', arguments: {handoff_id: first.id, scope: 'private'}});
    if (!crossScope.isError) throw new Error('Handoff scope boundary failed');
    outcomes.work_handoff = {id: first.id, source: 'c2-codex', receiver: receipt.received_by,
      from_revision: 1, to_revision: revised.revision, receipt: receipt.id, cross_scope_rejected: true};
  }
  if (embedding) {
    await embedding.connect();
    const query = '电脑坏了以后怎样把资料救回来？';
    const encoded = decoded(await embedding.client.callTool({name: 'embed_texts', arguments: {texts: [query], role: 'query'}}, undefined, {timeout: 180000}));
    const result = decoded(await client.client.callTool({name: 'search_knowledge_context', arguments: {query, plane: 'NOTES',
      embedding_query: {space: encoded.space, vector: encoded.items[0].vector}}}));
    if (!result.retrieval.vector_search || !result.combined_prompt.includes('创建一致性快照')) throw new Error('Shared real-vector retrieval failed');
    outcomes.shared_codex_real_embedding_retrieved = true;
    outcomes.query_dimension = encoded.items[0].vector.length;
  }
  await fs.writeFile(reportPath, JSON.stringify(outcomes, null, 2));
  console.log(JSON.stringify(outcomes));
} finally {
  if (embedding) await embedding.disconnect();
  await client.disconnect();
}
