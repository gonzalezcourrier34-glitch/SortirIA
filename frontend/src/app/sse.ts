import { SearchStreamEvent } from './search.model';


// # Lecture des Server-Sent Events (reprise de Chess Agent)

const KNOWN_EVENTS = ['progress', 'completed', 'error'];


/**
 * Découpe le texte reçu en messages complets.
 * Renvoie les messages et le reste (message pas encore terminé).
 */
export function splitSseBuffer(
    buffer: string
): { chunks: string[]; rest: string } {
    const chunks = buffer.split(/\r?\n\r?\n/);
    const rest = chunks.pop() ?? '';

    return { chunks, rest };
}


/** Lit un message « event: … / data: … » ; null s'il est inconnu ou incomplet. */
export function parseSseEvent(
    chunk: string
): SearchStreamEvent | null {
    let eventType: string | null = null;
    const dataLines: string[] = [];

    for (const line of chunk.split(/\r?\n/)) {
        if (line.startsWith('event:')) {
            eventType = line.slice('event:'.length).trim();
            continue;
        }

        if (line.startsWith('data:')) {
            dataLines.push(line.slice('data:'.length).trimStart());
        }
    }

    if (
        eventType === null
        || dataLines.length === 0
        || !KNOWN_EVENTS.includes(eventType)
    ) {
        return null;
    }

    return JSON.parse(dataLines.join('\n')) as SearchStreamEvent;
}
