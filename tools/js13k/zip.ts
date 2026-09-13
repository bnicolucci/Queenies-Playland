import { deflateRawSync } from 'node:zlib';
import { deflateAsync as zopfliDeflate } from '@gfx/zopfli';

// A minimal ZIP writer. js13k measures the zip, and there is no `zip` binary on
// every machine, so the container is written by hand: one local header per
// entry, a central directory, and an end-of-central-directory record.
//
// Entries are deflated with zopfli (same deflate format, slower and tighter
// encoder; zlib level 9 as a floor) and stored raw if that came out no smaller.

const CRC_TABLE = (() => {
    const table = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
        let c = n;
        for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
        table[n] = c >>> 0;
    }
    return table;
})();

function crc32(data: Uint8Array): number {
    let c = 0xffffffff;
    for (const byte of data) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
}

export type ZipEntry = { name: string; data: Uint8Array };

/** DOS timestamp. Fixed rather than "now", so the same input zips to the same
    bytes — a changing size should mean the content changed. */
const DOS_TIME = 0;
const DOS_DATE = 0x21; // 1980-01-01

export async function makeZip(entries: readonly ZipEntry[]): Promise<Buffer> {
    const locals: Buffer[] = [];
    const centrals: Buffer[] = [];
    let offset = 0;

    for (const entry of entries) {
        const name = Buffer.from(entry.name, 'utf8');
        const raw = Buffer.from(entry.data);
        const zlib = deflateRawSync(raw, { level: 9 });
        const zopfli = Buffer.from(await zopfliDeflate(raw, { numiterations: 100 }));
        const deflated = zopfli.length < zlib.length ? zopfli : zlib;
        const stored = deflated.length >= raw.length;
        const body = stored ? raw : deflated;
        const method = stored ? 0 : 8;
        const crc = crc32(raw);

        const local = Buffer.alloc(30 + name.length);
        local.writeUInt32LE(0x04034b50, 0);
        local.writeUInt16LE(20, 4); // version needed
        local.writeUInt16LE(0, 6); // flags
        local.writeUInt16LE(method, 8);
        local.writeUInt16LE(DOS_TIME, 10);
        local.writeUInt16LE(DOS_DATE, 12);
        local.writeUInt32LE(crc, 14);
        local.writeUInt32LE(body.length, 18);
        local.writeUInt32LE(raw.length, 22);
        local.writeUInt16LE(name.length, 26);
        local.writeUInt16LE(0, 28); // extra field length
        name.copy(local, 30);
        locals.push(local, body);

        const central = Buffer.alloc(46 + name.length);
        central.writeUInt32LE(0x02014b50, 0);
        central.writeUInt16LE(20, 4); // version made by
        central.writeUInt16LE(20, 6); // version needed
        central.writeUInt16LE(0, 8);
        central.writeUInt16LE(method, 10);
        central.writeUInt16LE(DOS_TIME, 12);
        central.writeUInt16LE(DOS_DATE, 14);
        central.writeUInt32LE(crc, 16);
        central.writeUInt32LE(body.length, 20);
        central.writeUInt32LE(raw.length, 24);
        central.writeUInt16LE(name.length, 28);
        central.writeUInt16LE(0, 30); // extra
        central.writeUInt16LE(0, 32); // comment
        central.writeUInt16LE(0, 34); // disk
        central.writeUInt16LE(0, 36); // internal attrs
        central.writeUInt32LE(0, 38); // external attrs
        central.writeUInt32LE(offset, 42);
        name.copy(central, 46);
        centrals.push(central);

        offset += local.length + body.length;
    }

    const centralBuf = Buffer.concat(centrals);
    const end = Buffer.alloc(22);
    end.writeUInt32LE(0x06054b50, 0);
    end.writeUInt16LE(0, 4);
    end.writeUInt16LE(0, 6);
    end.writeUInt16LE(entries.length, 8);
    end.writeUInt16LE(entries.length, 10);
    end.writeUInt32LE(centralBuf.length, 12);
    end.writeUInt32LE(offset, 16);
    end.writeUInt16LE(0, 20);

    return Buffer.concat([...locals, centralBuf, end]);
}
