import { describe, expect, it } from 'vitest';

import { parseSseEvent, splitSseBuffer } from './sse';


describe('lecture du flux de recherche', () => {
    it('garde le message incomplet pour la suite', () => {
        const { chunks, rest } = splitSseBuffer(
            'event: progress\ndata: {"event":"progress"}\n\nevent: comp'
        );

        expect(chunks).toHaveLength(1);
        expect(rest).toBe('event: comp');
    });

    it('lit un message de progression', () => {
        const event = parseSseEvent(
            'event: progress\ndata: {"event":"progress","step":"rechercher","status":"running","message":""}'
        );

        expect(event).toEqual({
            event: 'progress',
            step: 'rechercher',
            status: 'running',
            message: ''
        });
    });

    it('ignore un message inconnu', () => {
        expect(parseSseEvent('event: ping\ndata: {}')).toBeNull();
        expect(parseSseEvent('data: {}')).toBeNull();
    });
});
