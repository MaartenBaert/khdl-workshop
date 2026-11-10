// AXI4 protocol monitor for bounded formal checks with Yosys.
//
// Written from the AMBA AXI specification (Arm IHI 0022H, Part A), not from the
// KHDL implementation it checks. One instance watches one AXI4 interface. Rules
// the DUT must follow are assertions; rules of the environment on the other side
// are assumptions. DUT_IS_MANAGER selects which side is the DUT.
//
// Checked rules (spec sections in brackets):
//   reset     VALID is low during reset and at the first edge after it [A3.1.2]
//   handshake VALID stays asserted with a stable payload until READY [A3.2.1]
//   requests  BURST not reserved, SIZE within the bus, FIXED 1-16 beats, WRAP
//             2/4/8/16 beats with SIZE-aligned address, no 4KB crossing [A3.4.1];
//             exclusive accesses of a power-of-two byte count up to 128 bytes,
//             at most 16 beats, aligned to their size [A7.2.4]; AxCACHE
//             allocate bits only with the Modifiable bit [A4.4, Table A4-5]
//   W         bursts follow AW order and may precede their AW [A3.3, A5.3];
//             WLAST on beat AWLEN only; WSTRB only on that beat's byte lanes
//             [A3.4.2, A3.4.3]
//   R         RID matches an outstanding read; same-ID data in request order;
//             different IDs may interleave; RLAST on beat ARLEN only [A3.3.1,
//             A5.3]
//   B         BID matches an outstanding write whose AW and final W handshakes
//             both completed before BVALID; same-ID responses in order [A3.3.1,
//             A5.3]
//   EXOKAY    only for exclusive requests [A7.2.3]
//
// Not checked: liveness (including the rule that a Manager must not wait for
// READY before asserting VALID), exclusive-monitor semantics, X propagation,
// attribute meanings, and USER contents. Tracking is bounded by MAX_READS,
// MAX_WRITES, and MAX_EARLY_BEATS; exceeding a bound is an environment
// assumption or a DUT assertion, so an undersized monitor fails loudly instead
// of silently dropping checks. These bounds are harness limits, not AXI rules.
//
// Payloads use the KHDL packing: the first struct field in the low bits.
//   AW/AR: id, addr, len[8], size[3], burst[2], lock, cache[4], prot[3], qos[4],
//          region[4], user        W: data, strb, last, user
//   B: id, resp[2], user          R: id, data, resp[2], last, user
module axi4_protocol #(
    parameter integer ID_WIDTH = 1,
    parameter integer ADDR_WIDTH = 12,
    parameter integer DATA_WIDTH = 16,
    parameter integer AWUSER_WIDTH = 0,
    parameter integer WUSER_WIDTH = 0,
    parameter integer BUSER_WIDTH = 0,
    parameter integer ARUSER_WIDTH = 0,
    parameter integer RUSER_WIDTH = 0,
    parameter integer DUT_IS_MANAGER = 0,
    parameter integer MAX_READS = 4,
    parameter integer MAX_WRITES = 4,
    parameter integer MAX_EARLY_BEATS = 4
) (
    input wire clk,
    input wire rst,
    input wire [ID_WIDTH+ADDR_WIDTH+29+AWUSER_WIDTH-1:0] aw,
    input wire awvalid,
    input wire awready,
    input wire [DATA_WIDTH+DATA_WIDTH/8+1+WUSER_WIDTH-1:0] w,
    input wire wvalid,
    input wire wready,
    input wire [ID_WIDTH+2+BUSER_WIDTH-1:0] b,
    input wire bvalid,
    input wire bready,
    input wire [ID_WIDTH+ADDR_WIDTH+29+ARUSER_WIDTH-1:0] ar,
    input wire arvalid,
    input wire arready,
    input wire [ID_WIDTH+DATA_WIDTH+3+RUSER_WIDTH-1:0] r,
    input wire rvalid,
    input wire rready,
    // Observation outputs for reachability checks.
    output wire [7:0] reads_outstanding,
    output wire [7:0] writes_outstanding,
    output wire [7:0] early_beats
);
    localparam integer STRB_WIDTH = DATA_WIDTH / 8;
    localparam integer MAX_SIZE = $clog2(STRB_WIDTH);
    localparam integer A = ID_WIDTH + ADDR_WIDTH;  // offset of LEN in AW/AR
    localparam [1:0] FIXED = 2'd0, INCR = 2'd1, WRAP = 2'd2, EXOKAY = 2'd1;

    // Assertions for the DUT's side, assumptions for the environment's side.
`define AXI4_MANAGER_RULE(condition) \
    if (DUT_IS_MANAGER != 0) assert(condition); else assume(condition);
`define AXI4_SUBORDINATE_RULE(condition) \
    if (DUT_IS_MANAGER != 0) assume(condition); else assert(condition);

    wire [ID_WIDTH-1:0] awid = aw[0 +: ID_WIDTH];
    wire [ADDR_WIDTH-1:0] awaddr = aw[ID_WIDTH +: ADDR_WIDTH];
    wire [7:0] awlen = aw[A +: 8];
    wire [2:0] awsize = aw[A+8 +: 3];
    wire [1:0] awburst = aw[A+11 +: 2];
    wire awlock = aw[A+13];
    wire [3:0] awcache = aw[A+14 +: 4];
    wire [ID_WIDTH-1:0] arid = ar[0 +: ID_WIDTH];
    wire [ADDR_WIDTH-1:0] araddr = ar[ID_WIDTH +: ADDR_WIDTH];
    wire [7:0] arlen = ar[A +: 8];
    wire [2:0] arsize = ar[A+8 +: 3];
    wire [1:0] arburst = ar[A+11 +: 2];
    wire arlock = ar[A+13];
    wire [3:0] arcache = ar[A+14 +: 4];
    wire [STRB_WIDTH-1:0] wstrb = w[DATA_WIDTH +: STRB_WIDTH];
    wire wlast = w[DATA_WIDTH+STRB_WIDTH];
    wire [ID_WIDTH-1:0] bid = b[0 +: ID_WIDTH];
    wire [1:0] bresp = b[ID_WIDTH +: 2];
    wire [ID_WIDTH-1:0] rid = r[0 +: ID_WIDTH];
    wire [1:0] rresp = r[ID_WIDTH+DATA_WIDTH +: 2];
    wire rlast = r[ID_WIDTH+DATA_WIDTH+2];

    // Request legality [A3.4.1, A4.4, A7.2.4].
    function legal_request;
        input [ADDR_WIDTH-1:0] addr;
        input [7:0] len;
        input [2:0] size;
        input [1:0] burst;
        input lock;
        input [3:0] cache;
        reg [31:0] bytes, beats, total, aligned, last_byte;
        begin
            bytes = 32'd1 << size;
            beats = {24'd0, len} + 32'd1;
            total = beats << size;
            aligned = addr & ~(bytes - 32'd1);
            last_byte = aligned + total - 32'd1;
            legal_request = size <= MAX_SIZE && burst != 2'd3;
            if (burst == FIXED && len > 8'd15)
                legal_request = 1'b0;
            if (burst == WRAP && !((len == 8'd1 || len == 8'd3 || len == 8'd7 || len == 8'd15)
                    && (addr & (bytes - 32'd1)) == 0))
                legal_request = 1'b0;
            // No 4KB crossing. An address space narrower than 4KB may not be
            // exceeded either; wider spaces end on a 4KB boundary anyway.
            if (burst == INCR && ((aligned >> 12) != (last_byte >> 12)
                    || (last_byte >> ADDR_WIDTH) != 0))
                legal_request = 1'b0;
            if (lock && !((beats == 1 || beats == 2 || beats == 4 || beats == 8 || beats == 16)
                    && total <= 128 && (addr & (total - 32'd1)) == 0))
                legal_request = 1'b0;
            if (!cache[1] && cache[3:2] != 2'd0)
                legal_request = 1'b0;
        end
    endfunction

    // Byte lanes of beat number `beat` (zero-based) [A3.4.2]. Lanes depend only
    // on address bits below 4KB (a WRAP burst spans at most 2KB), so 12-bit
    // arithmetic suffices; shifts replace the spec's multiplications because
    // transfer sizes are powers of two.
    function [STRB_WIDTH-1:0] lane_mask;
        input [ADDR_WIDTH-1:0] addr;
        input [7:0] len;
        input [2:0] size;
        input [1:0] burst;
        input [8:0] beat;
        reg [11:0] start, bytes, total, aligned, address, lower, upper;
        integer lane;
        begin
            start = addr;
            bytes = 12'd1 << size;
            total = ({4'd0, len} + 12'd1) << size;
            aligned = start & ~(bytes - 12'd1);
            if (burst == FIXED || beat == 0)
                address = start;
            else if (burst == WRAP)
                address = (aligned & ~(total - 12'd1)) | ((aligned + ({3'd0, beat} << size)) & (total - 12'd1));
            else
                address = aligned + ({3'd0, beat} << size);
            lower = address & (STRB_WIDTH - 1);
            upper = (address & ~(bytes - 12'd1) & (STRB_WIDTH - 1)) + bytes - 12'd1;
            lane_mask = {STRB_WIDTH{1'b0}};
            for (lane = 0; lane < STRB_WIDTH; lane = lane + 1)
                if (lane >= lower && lane <= upper)
                    lane_mask[lane] = 1'b1;
        end
    endfunction

    // Outstanding reads, oldest first. An entry retires after its final beat.
    reg [7:0] rd_count = 8'd0;
    reg [MAX_READS*ID_WIDTH-1:0] rd_id;
    reg [MAX_READS*8-1:0] rd_len, rd_beats;
    reg [MAX_READS-1:0] rd_lock;
    // Outstanding writes in AW order. An entry retires on its B response; `done`
    // marks a complete W burst. Done entries always form a prefix, because W
    // bursts complete in AW order.
    reg [7:0] wr_count = 8'd0;
    reg [MAX_WRITES*ID_WIDTH-1:0] wr_id;
    reg [MAX_WRITES*ADDR_WIDTH-1:0] wr_addr;
    reg [MAX_WRITES*8-1:0] wr_len;
    reg [MAX_WRITES*9-1:0] wr_beats;
    reg [MAX_WRITES*3-1:0] wr_size;
    reg [MAX_WRITES*2-1:0] wr_burst;
    reg [MAX_WRITES-1:0] wr_lock, wr_done;
    // W beats of the AW currently presented but not yet accepted. That AW is
    // the next in order and its payload is stable, so its beats are checked
    // against it immediately.
    reg [8:0] pend_beats = 9'd0;
    reg pend_done = 1'b0;
    // W beats transferred before any AW for them was presented, in order.
    reg [7:0] early_count = 8'd0;
    reg [MAX_EARLY_BEATS*STRB_WIDTH-1:0] early_strb;
    reg [MAX_EARLY_BEATS-1:0] early_last;

    assign reads_outstanding = rd_count;
    assign writes_outstanding = wr_count;
    assign early_beats = early_count;

    // Previous-edge observations for the reset and handshake rules.
    reg past_rst = 1'b0;
    reg past_aw_stall = 1'b0, past_w_stall = 1'b0, past_b_stall = 1'b0;
    reg past_ar_stall = 1'b0, past_r_stall = 1'b0;
    reg [ID_WIDTH+ADDR_WIDTH+29+AWUSER_WIDTH-1:0] past_aw;
    reg [DATA_WIDTH+DATA_WIDTH/8+1+WUSER_WIDTH-1:0] past_w;
    reg [ID_WIDTH+2+BUSER_WIDTH-1:0] past_b;
    reg [ID_WIDTH+ADDR_WIDTH+29+ARUSER_WIDTH-1:0] past_ar;
    reg [ID_WIDTH+DATA_WIDTH+3+RUSER_WIDTH-1:0] past_r;

    // Response matching against registered state: a response in the same cycle
    // as its request handshake is a violation, as the spec requires.
    integer i, j;
    reg r_hit, b_hit;
    reg [7:0] r_index, b_index;
    always @(*) begin
        r_hit = 1'b0; r_index = 8'd0;
        b_hit = 1'b0; b_index = 8'd0;
        // Descending, so the oldest matching entry wins.
        for (i = MAX_READS - 1; i >= 0; i = i - 1)
            if (i < rd_count && rd_id[i*ID_WIDTH +: ID_WIDTH] == rid) begin
                r_hit = 1'b1; r_index = i;
            end
        for (i = MAX_WRITES - 1; i >= 0; i = i - 1)
            if (i < wr_count && wr_id[i*ID_WIDTH +: ID_WIDTH] == bid) begin
                b_hit = 1'b1; b_index = i;
            end
    end

    // Next table state, and W violations found while assigning W beats.
    reg [7:0] n_rd_count, n_wr_count, n_early_count, consumed, target;
    reg [MAX_READS*ID_WIDTH-1:0] n_rd_id;
    reg [MAX_READS*8-1:0] n_rd_len, n_rd_beats;
    reg [MAX_READS-1:0] n_rd_lock;
    reg [MAX_WRITES*ID_WIDTH-1:0] n_wr_id;
    reg [MAX_WRITES*ADDR_WIDTH-1:0] n_wr_addr;
    reg [MAX_WRITES*8-1:0] n_wr_len;
    reg [MAX_WRITES*9-1:0] n_wr_beats;
    reg [MAX_WRITES*3-1:0] n_wr_size;
    reg [MAX_WRITES*2-1:0] n_wr_burst;
    reg [MAX_WRITES-1:0] n_wr_lock, n_wr_done;
    reg [MAX_EARLY_BEATS*STRB_WIDTH-1:0] n_early_strb;
    reg [MAX_EARLY_BEATS-1:0] n_early_last;
    reg found, pending, wlast_error, wstrb_error, beat_last, n_pend_done;
    reg [STRB_WIDTH-1:0] beat_strb;
    reg [8:0] beat_count, n_pend_beats;
    reg [ADDR_WIDTH-1:0] t_addr;
    reg [7:0] t_len;
    reg [2:0] t_size;
    reg [1:0] t_burst;

    always @(*) begin
        n_rd_count = rd_count; n_rd_id = rd_id; n_rd_len = rd_len;
        n_rd_beats = rd_beats; n_rd_lock = rd_lock;
        n_wr_count = wr_count; n_wr_id = wr_id; n_wr_addr = wr_addr; n_wr_len = wr_len;
        n_wr_beats = wr_beats; n_wr_size = wr_size; n_wr_burst = wr_burst;
        n_wr_lock = wr_lock; n_wr_done = wr_done;
        n_early_count = early_count; n_early_strb = early_strb; n_early_last = early_last;
        wlast_error = 1'b0; wstrb_error = 1'b0;
        n_pend_beats = pend_beats; n_pend_done = pend_done;
        consumed = 8'd0; found = 1'b0; pending = 1'b0; target = 8'd0;
        t_addr = {ADDR_WIDTH{1'b0}}; t_len = 8'd0; t_size = 3'd0; t_burst = 2'd0;
        beat_last = 1'b0; beat_strb = {STRB_WIDTH{1'b0}}; beat_count = 9'd0;

        // R: one beat of the oldest read with this ID; retire it after LEN+1 beats.
        if (rvalid && rready && r_hit) begin
            if (rd_beats[r_index*8 +: 8] == rd_len[r_index*8 +: 8]) begin
                for (i = 0; i < MAX_READS - 1; i = i + 1)
                    if (i >= r_index) begin
                        n_rd_id[i*ID_WIDTH +: ID_WIDTH] = n_rd_id[(i+1)*ID_WIDTH +: ID_WIDTH];
                        n_rd_len[i*8 +: 8] = n_rd_len[(i+1)*8 +: 8];
                        n_rd_beats[i*8 +: 8] = n_rd_beats[(i+1)*8 +: 8];
                        n_rd_lock[i] = n_rd_lock[i+1];
                    end
                n_rd_count = n_rd_count - 8'd1;
            end else begin
                n_rd_beats[r_index*8 +: 8] = rd_beats[r_index*8 +: 8] + 8'd1;
            end
        end
        if (arvalid && arready)
            for (i = 0; i < MAX_READS; i = i + 1)
                if (i == n_rd_count) begin
                    n_rd_id[i*ID_WIDTH +: ID_WIDTH] = arid;
                    n_rd_len[i*8 +: 8] = arlen;
                    n_rd_beats[i*8 +: 8] = 8'd0;
                    n_rd_lock[i] = arlock;
                end
        if (arvalid && arready && n_rd_count < MAX_READS)
            n_rd_count = n_rd_count + 8'd1;

        // B: retire the oldest write with this ID.
        if (bvalid && bready && b_hit) begin
            for (i = 0; i < MAX_WRITES - 1; i = i + 1)
                if (i >= b_index) begin
                    n_wr_id[i*ID_WIDTH +: ID_WIDTH] = n_wr_id[(i+1)*ID_WIDTH +: ID_WIDTH];
                    n_wr_addr[i*ADDR_WIDTH +: ADDR_WIDTH] = n_wr_addr[(i+1)*ADDR_WIDTH +: ADDR_WIDTH];
                    n_wr_len[i*8 +: 8] = n_wr_len[(i+1)*8 +: 8];
                    n_wr_beats[i*9 +: 9] = n_wr_beats[(i+1)*9 +: 9];
                    n_wr_size[i*3 +: 3] = n_wr_size[(i+1)*3 +: 3];
                    n_wr_burst[i*2 +: 2] = n_wr_burst[(i+1)*2 +: 2];
                    n_wr_lock[i] = n_wr_lock[i+1];
                    n_wr_done[i] = n_wr_done[i+1];
                end
            n_wr_count = n_wr_count - 8'd1;
        end
        if (awvalid && awready)
            for (i = 0; i < MAX_WRITES; i = i + 1)
                if (i == n_wr_count) begin
                    n_wr_id[i*ID_WIDTH +: ID_WIDTH] = awid;
                    n_wr_addr[i*ADDR_WIDTH +: ADDR_WIDTH] = awaddr;
                    n_wr_len[i*8 +: 8] = awlen;
                    n_wr_beats[i*9 +: 9] = pend_beats;
                    n_wr_size[i*3 +: 3] = awsize;
                    n_wr_burst[i*2 +: 2] = awburst;
                    n_wr_lock[i] = awlock;
                    n_wr_done[i] = pend_done;
                end
        if (awvalid && awready && n_wr_count < MAX_WRITES)
            n_wr_count = n_wr_count + 8'd1;
        if (awvalid && awready) begin
            n_pend_beats = 9'd0; n_pend_done = 1'b0;
        end

        // Assign early W beats, oldest first, then the current beat. A beat
        // belongs to the oldest accepted write with incomplete data, otherwise
        // to the presented AW. Each assigned beat is checked against its AW.
        for (j = 0; j <= MAX_EARLY_BEATS; j = j + 1) begin
            found = 1'b0; pending = 1'b0; target = 8'd0;
            for (i = MAX_WRITES - 1; i >= 0; i = i - 1)
                if (i < n_wr_count && !n_wr_done[i]) begin
                    found = 1'b1; target = i;
                end
            if (!found && awvalid && !awready && !n_pend_done) begin
                found = 1'b1; pending = 1'b1;
            end
            if (j < MAX_EARLY_BEATS) begin
                beat_strb = early_strb[j*STRB_WIDTH +: STRB_WIDTH];
                beat_last = early_last[j];
                found = found && j < early_count;
            end else begin
                // The current beat, if all earlier beats found their AW.
                beat_strb = wstrb;
                beat_last = wlast;
                found = found && wvalid && consumed == early_count;
            end
            if (pending) begin
                t_addr = awaddr; t_len = awlen; t_size = awsize; t_burst = awburst;
                beat_count = n_pend_beats;
            end else begin
                t_addr = n_wr_addr[target*ADDR_WIDTH +: ADDR_WIDTH];
                t_len = n_wr_len[target*8 +: 8];
                t_size = n_wr_size[target*3 +: 3];
                t_burst = n_wr_burst[target*2 +: 2];
                beat_count = n_wr_beats[target*9 +: 9];
            end
            if (found) begin
                if ((beat_strb & ~lane_mask(t_addr, t_len, t_size, t_burst, beat_count)) != 0)
                    wstrb_error = 1'b1;
                if (beat_last != (beat_count == {1'b0, t_len}))
                    wlast_error = 1'b1;
                // The current beat updates state only when it transfers.
                if (j < MAX_EARLY_BEATS || wready) begin
                    if (beat_count == {1'b0, t_len}) begin
                        if (pending) n_pend_done = 1'b1;
                        else n_wr_done[target] = 1'b1;
                    end else begin
                        if (pending) n_pend_beats = beat_count + 9'd1;
                        else n_wr_beats[target*9 +: 9] = beat_count + 9'd1;
                    end
                    if (j < MAX_EARLY_BEATS)
                        consumed = consumed + 8'd1;
                end
            end
        end
        n_early_strb = early_strb >> (consumed * STRB_WIDTH);
        n_early_last = early_last >> consumed;
        n_early_count = early_count - consumed;
        // A transferred beat without an AW yet joins the early queue.
        if (wvalid && wready && !(found && consumed == early_count))
            for (i = 0; i < MAX_EARLY_BEATS; i = i + 1)
                if (i == n_early_count) begin
                    n_early_strb[i*STRB_WIDTH +: STRB_WIDTH] = wstrb;
                    n_early_last[i] = wlast;
                end
        if (wvalid && wready && !(found && consumed == early_count)
                && n_early_count < MAX_EARLY_BEATS)
            n_early_count = n_early_count + 8'd1;
    end

    // Each rule is a named signal, so counterexample traces show which failed.
    wire normal = !rst && !past_rst;
    wire manager_reset_ok = !(rst || past_rst) || (!awvalid && !wvalid && !arvalid);
    wire subordinate_reset_ok = !(rst || past_rst) || (!bvalid && !rvalid);
    wire aw_stable_ok = !(normal && past_aw_stall) || (awvalid && aw == past_aw);
    wire w_stable_ok = !(normal && past_w_stall) || (wvalid && w == past_w);
    wire ar_stable_ok = !(normal && past_ar_stall) || (arvalid && ar == past_ar);
    wire b_stable_ok = !(normal && past_b_stall) || (bvalid && b == past_b);
    wire r_stable_ok = !(normal && past_r_stall) || (rvalid && r == past_r);
    wire aw_legal_ok = rst || !awvalid || legal_request(awaddr, awlen, awsize, awburst, awlock, awcache);
    wire ar_legal_ok = rst || !arvalid || legal_request(araddr, arlen, arsize, arburst, arlock, arcache);
    wire wlast_ok = rst || !wlast_error;
    wire wstrb_ok = rst || !wstrb_error;
    // Monitor capacity, not AXI rules.
    wire capacity_ok = rst || !((arvalid && rd_count >= MAX_READS)
        || (awvalid && wr_count >= MAX_WRITES) || (wvalid && early_count >= MAX_EARLY_BEATS));
    wire rid_ok = rst || !rvalid || r_hit;
    wire rlast_ok = rst || !(rvalid && r_hit)
        || rlast == (rd_beats[r_index*8 +: 8] == rd_len[r_index*8 +: 8]);
    wire rresp_ok = rst || !(rvalid && r_hit) || rresp != EXOKAY || rd_lock[r_index];
    wire bid_ok = rst || !bvalid || b_hit;
    wire b_after_w_ok = rst || !(bvalid && b_hit) || wr_done[b_index];
    wire bresp_ok = rst || !(bvalid && b_hit) || bresp != EXOKAY || wr_lock[b_index];

    always @(*) begin
        `AXI4_MANAGER_RULE(manager_reset_ok)
        `AXI4_MANAGER_RULE(aw_stable_ok)
        `AXI4_MANAGER_RULE(w_stable_ok)
        `AXI4_MANAGER_RULE(ar_stable_ok)
        `AXI4_MANAGER_RULE(aw_legal_ok)
        `AXI4_MANAGER_RULE(ar_legal_ok)
        `AXI4_MANAGER_RULE(wlast_ok)
        `AXI4_MANAGER_RULE(wstrb_ok)
        `AXI4_MANAGER_RULE(capacity_ok)
        `AXI4_SUBORDINATE_RULE(subordinate_reset_ok)
        `AXI4_SUBORDINATE_RULE(b_stable_ok)
        `AXI4_SUBORDINATE_RULE(r_stable_ok)
        `AXI4_SUBORDINATE_RULE(rid_ok)
        `AXI4_SUBORDINATE_RULE(rlast_ok)
        `AXI4_SUBORDINATE_RULE(rresp_ok)
        `AXI4_SUBORDINATE_RULE(bid_ok)
        `AXI4_SUBORDINATE_RULE(b_after_w_ok)
        `AXI4_SUBORDINATE_RULE(bresp_ok)
    end

    always @(posedge clk) begin
        past_rst <= rst;
        past_aw_stall <= !rst && awvalid && !awready;
        past_w_stall <= !rst && wvalid && !wready;
        past_b_stall <= !rst && bvalid && !bready;
        past_ar_stall <= !rst && arvalid && !arready;
        past_r_stall <= !rst && rvalid && !rready;
        past_aw <= aw; past_w <= w; past_b <= b; past_ar <= ar; past_r <= r;
        if (rst) begin
            rd_count <= 8'd0;
            wr_count <= 8'd0;
            early_count <= 8'd0;
            pend_beats <= 9'd0;
            pend_done <= 1'b0;
        end else begin
            rd_count <= n_rd_count; rd_id <= n_rd_id; rd_len <= n_rd_len;
            rd_beats <= n_rd_beats; rd_lock <= n_rd_lock;
            wr_count <= n_wr_count; wr_id <= n_wr_id; wr_addr <= n_wr_addr;
            wr_len <= n_wr_len; wr_beats <= n_wr_beats; wr_size <= n_wr_size;
            wr_burst <= n_wr_burst; wr_lock <= n_wr_lock; wr_done <= n_wr_done;
            early_count <= n_early_count; early_strb <= n_early_strb;
            early_last <= n_early_last;
            pend_beats <= n_pend_beats; pend_done <= n_pend_done;
        end
    end

`undef AXI4_MANAGER_RULE
`undef AXI4_SUBORDINATE_RULE
endmodule
