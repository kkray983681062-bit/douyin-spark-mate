async (args) => {
    // Adapt the already authenticated official IM runtime. No captured tokens,
    // message templates, or site implementation are copied into the application.
    const slot = Symbol.for('spark-mate.im-bridge.v1');
    const state = window[slot] || (window[slot] = {jobs: new Map()});
    const fail = code => { throw new Error(code); };
    const positive = value => /^[1-9]\d*$/.test(String(value ?? ''));
    function runtime() {
        const names = Object.keys(window).filter(k => k.startsWith('@pc-im/im:') && Array.isArray(window[k]));
        if (names.length !== 1) return null;
        const chunks = window[names[0]];
        if (state.chunks !== chunks) {
            const candidates = new Map();
            for (const entry of chunks) {
                for (const [id, fn] of Object.entries(entry[1] || {})) {
                    const source = typeof fn === 'function' ? String(fn) : '';
                    if (source.includes('getImSdkInstance') && source.includes('ImSdkInstance:null') && source.length < 4000)
                        candidates.set(id, fn);
                }
            }
            if (candidates.size !== 1) return null;
            let require;
            chunks.push([[`spark-mate-${crypto.randomUUID()}`], {}, value => { require = value; }]);
            const exports = require?.([...candidates.keys()][0]);
            if (typeof exports?.o !== 'function') return null;
            state.chunks = chunks;
            state.getSdk = exports.o;
        }
        const sdk = state.getSdk(true);
        if (!sdk || sdk.disposed || sdk.ctx?.initResult !== 3 || !positive(sdk.ctx?.option?.userId)) return null;
        if (!['getConversation', 'getConversationList', 'createMessage', 'sendMessage'].every(k => typeof sdk[k] === 'function'))
            return null;
        return sdk;
    }
    function account(sdk) {
        if (!sdk) fail('unavailable');
        if (String(sdk.ctx.option.userId) !== args.user_id) fail('account_changed');
    }
    function conversation(sdk) {
        account(sdk);
        if (!/^0:1:\d+:\d+$/.test(args.target || '') || !args.target.split(':').slice(2).includes(args.user_id))
            fail('target_mismatch');
        const value = sdk.getConversation({conversationId: args.target});
        if (!value || value.id !== args.target || value.type !== 1 || !positive(value.shortId)) fail('target_mismatch');
        return value;
    }
    function messageMatches(job) {
        const m = job.message;
        return m && m.clientId === job.clientId && m.conversationId === job.target && m.conversationType === 1 &&
            String(m.conversationShortId) === job.shortId && String(m.sender) === job.userId &&
            m.type === 7 && m.content === job.content;
    }
    try {
        const sdk = runtime();
        if (args.op === 'status') {
            if (!sdk) return {ready: false};
            return {ready: true, user_id: String(sdk.ctx.option.userId),
                single_count: sdk.getConversationList().filter(c => c.type === 1).length};
        }
        if (args.op === 'target') { conversation(sdk); return {ok: true}; }
        if (args.op === 'prepare') {
            const conv = conversation(sdk);
            if (typeof args.text !== 'string' || !args.text.trim() || !args.client_id || state.jobs.has(args.client_id))
                fail('invalid_message');
            const content = JSON.stringify({text: args.text, mention_users: [], aweType: 700, richTextInfos: []});
            let timer;
            let message;
            try {
                message = await Promise.race([
                    sdk.createMessage({conversation: conv, content, type: 7, clientId: args.client_id,
                        insert: false, mentionedUsers: [], ext: {}}),
                    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('prepare_timeout')), 5000); })
                ]);
            } finally { clearTimeout(timer); }
            const job = {sdk, message, clientId: args.client_id, userId: args.user_id, target: args.target,
                shortId: String(conv.shortId), content, result: {state: 'prepared'}};
            if (!messageMatches(job) || message.serverId) fail('message_mismatch');
            state.jobs.set(args.client_id, job);
            return {state: 'prepared'};
        }
        const job = state.jobs.get(args.client_id);
        if (!job || job.target !== args.target || job.userId !== args.user_id) fail('missing_operation');
        if (args.op === 'poll') return job.result;
        if (args.op === 'start') {
            conversation(sdk);
            if (job.sdk !== sdk || !messageMatches(job)) fail('message_mismatch');
            if (job.result.state !== 'prepared') return job.result;
            job.result = {state: 'pending'};
            Promise.resolve().then(() => {
                account(sdk);
                return sdk.sendMessage({message: job.message});
            }).then(ack => {
                const body = ack?.body;
                if (!messageMatches(job) || ack?.payload !== job.message ||
                    (body?.client_message_id && String(body.client_message_id) !== job.clientId)) {
                    job.result = {state: 'unknown'};
                } else if (body && Number.isInteger(body.status) && body.status !== 0) {
                    job.result = {state: 'rejected', code: body.status};
                } else if (ack?.success === true && ack.statusCode === 0 && body?.status === 0 &&
                    positive(body.server_message_id) && String(body.server_message_id) === String(job.message.serverId) &&
                    [3, 4].includes(job.message.flightStatus)) {
                    job.result = {state: 'sent', client_id: job.clientId, server_id: String(job.message.serverId)};
                } else job.result = {state: 'unknown'};
            }).catch(() => { job.result = {state: 'unknown'}; });
            return job.result;
        }
        fail('unsupported_operation');
    } catch (error) {
        const known = ['unavailable', 'account_changed', 'target_mismatch', 'invalid_message',
            'prepare_timeout', 'message_mismatch', 'missing_operation', 'unsupported_operation'];
        return {error: known.includes(error.message) ? error.message : 'runtime_error'};
    }
}
