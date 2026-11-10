// AXI3 and AXI4-Lite views for the AXI4 protocol monitor (axi4_protocol.sv).
//
// Written from the AMBA AXI specification (Arm IHI 0022H: Part A for AXI4 and
// AXI3, Part B for AXI4-Lite), not from the KHDL implementation. The module
// embeds each observed payload in the AXI4 layout the monitor expects, so every
// AXI4 rule applies with its meaning unchanged, and checks the AXI3 rules that
// AXI4 lacks:
//
//   AXI4-Lite [B1.1]  A transaction is one full-width beat with no ID, LAST,
//             attributes other than PROT, or USER. Embedded as ID 0, LEN 0,
//             SIZE = bus width, INCR, normal access, CACHE 0, QOS/REGION 0, and
//             LAST set. The AXI4 same-ID ordering rules then require in-order
//             responses, which is the AXI4-Lite rule.
//   AXI3 [A3.4.1, A7.4]  AxLEN has 4 bits, embedded zero-extended (at most 16
//             beats). AxLOCK has 2 bits: 0 normal, 1 exclusive, 2 locked, 3
//             reserved; exclusive maps to the AXI4 LOCK bit, locked to a normal
//             access (the AXI4 monitor has no locked transactions), and 3 is a
//             rule violation here. No QoS or region (embedded as 0).
//   AXI3 WID [A5.3]  Every beat of a write burst carries the same WID, and the
//             k-th write burst's WID equals the k-th AW's AWID, also when W
//             precedes its AW. (The DUTs do not interleave write data; a
//             Manager that did would fail this rule, as an interleaving depth
//             of one is declared.)
//
// AXI3 USER signals are not supported here (all USER widths must be zero).
// Tracking is bounded by MAX_WRITES pending AWs and MAX_EARLY_BEATS write bursts
// started before their AW, matching the monitor's bounds; exceeding a bound is
// a rule failure of the side that caused it.
//
// Payloads use the KHDL packing: the first struct field in the low bits.
//   AXI3 AW/AR: id, addr, len[4], size[3], burst[2], lock[2], cache[4], prot[3]
//   AXI3 W: id, data, strb, last     B: id, resp[2]     R: id, data, resp[2], last
//   Lite AW/AR: addr, prot[3]   W: data, strb   B: resp[2]   R: data, resp[2]
module axi_protocol_view #(
    parameter integer PROTOCOL = 1,          // 1 AXI3, 2 AXI4-Lite
    parameter integer ID_WIDTH = 1,          // bus ID width (AXI3); ignored for Lite
    parameter integer ADDR_WIDTH = 12,
    parameter integer DATA_WIDTH = 16,
    parameter integer DUT_IS_MANAGER = 0,
    parameter integer MAX_WRITES = 4,
    parameter integer MAX_EARLY_BEATS = 4,
    // ID width of the AXI4 view: the bus width, or 1 for Lite (constant 0).
    parameter integer VIEW_ID = PROTOCOL == 2 ? 1 : ID_WIDTH,
    parameter integer BUS_ID = PROTOCOL == 2 ? 0 : ID_WIDTH,
    parameter integer BUS_A = PROTOCOL == 2 ? ADDR_WIDTH + 3 : ID_WIDTH + ADDR_WIDTH + 18,
    parameter integer BUS_W = PROTOCOL == 2 ? DATA_WIDTH + DATA_WIDTH / 8 : ID_WIDTH + DATA_WIDTH + DATA_WIDTH / 8 + 1,
    parameter integer BUS_B = PROTOCOL == 2 ? 2 : ID_WIDTH + 2,
    parameter integer BUS_R = PROTOCOL == 2 ? DATA_WIDTH + 2 : ID_WIDTH + DATA_WIDTH + 3
) (
    input wire clk,
    input wire rst,
    input wire [BUS_A-1:0] aw, input wire awvalid, input wire awready,
    input wire [BUS_W-1:0] w, input wire wvalid, input wire wready,
    input wire [BUS_B-1:0] b, input wire bvalid, input wire bready,
    input wire [BUS_A-1:0] ar, input wire arvalid, input wire arready,
    input wire [BUS_R-1:0] r, input wire rvalid, input wire rready,
    output wire [VIEW_ID+ADDR_WIDTH+29-1:0] aw4,
    output wire [DATA_WIDTH+DATA_WIDTH/8+1-1:0] w4,
    output wire [VIEW_ID+2-1:0] b4,
    output wire [VIEW_ID+ADDR_WIDTH+29-1:0] ar4,
    output wire [VIEW_ID+DATA_WIDTH+3-1:0] r4
);
    localparam integer STRB = DATA_WIDTH / 8;
    localparam [2:0] FULL_SIZE = $clog2(STRB);

`define AXI3_MANAGER_RULE(condition) \
    if (DUT_IS_MANAGER) assert(condition); else assume(condition)

    generate if (PROTOCOL == 2) begin : lite
        // addr, prot -> id 0, addr, len 0, size, INCR, lock 0, cache 0, prot, qos 0, region 0
        assign aw4 = {8'd0, aw[ADDR_WIDTH+:3], 4'd0, 1'b0, 2'd1, FULL_SIZE, 8'd0, aw[ADDR_WIDTH-1:0], 1'b0};
        assign ar4 = {8'd0, ar[ADDR_WIDTH+:3], 4'd0, 1'b0, 2'd1, FULL_SIZE, 8'd0, ar[ADDR_WIDTH-1:0], 1'b0};
        assign w4 = {1'b1, w};                       // data, strb, last
        assign b4 = {b, 1'b0};                       // id 0, resp
        assign r4 = {1'b1, r, 1'b0};                 // id 0, data, resp, last
    end else begin : axi3
        localparam integer I = ID_WIDTH, A = ID_WIDTH + ADDR_WIDTH;
        function automatic [I+ADDR_WIDTH+29-1:0] embed(input [BUS_A-1:0] x);
            // id, addr, len (zero-extended), size, burst, exclusive, cache, prot, qos 0, region 0
            embed = {8'd0, x[A+15+:3], x[A+11+:4], x[A+9+:2] == 2'd1, x[A+7+:2], x[A+4+:3],
                     4'd0, x[A+:4], x[0+:A]};
        endfunction
        assign aw4 = embed(aw);
        assign ar4 = embed(ar);
        assign w4 = w[BUS_W-1:I];                    // drop WID: data, strb, last
        assign b4 = b;
        assign r4 = r;

        // AxLOCK 3 is reserved; AW and AR are Manager-driven.
        wire aw_lock_ok = rst || !awvalid || aw[A+9+:2] != 2'd3;
        wire ar_lock_ok = rst || !arvalid || ar[A+9+:2] != 2'd3;

        // WID tracking in two circular queues: AWs whose burst has not started,
        // and bursts started before their AW. At most one is non-empty.
        // Packed vectors rather than arrays: the AIGER export has no memories.
        reg [I*MAX_WRITES-1:0] aw_ids;
        reg [I*MAX_EARLY_BEATS-1:0] early_ids;
        reg [7:0] aw_head = 0, aw_count = 0, early_head = 0, early_count = 0;
        reg in_burst = 1'b0;
        reg [I-1:0] burst_id;
        wire aw_hs = awvalid && awready, w_hs = wvalid && wready;
        wire [I-1:0] awid = aw[0+:I], wid = w[0+:I];
        wire wlast = w[BUS_W-1];
        // An AW first matches the oldest early burst; otherwise it waits.
        wire aw_matches_early = aw_hs && early_count != 0;
        wire aw_waits = aw_hs && early_count == 0;
        wire [7:0] aw_available = aw_count + aw_waits;
        wire [I-1:0] aw_front = aw_count != 0 ? aw_ids[aw_head*I+:I] : awid;
        // The first beat of a burst matches the oldest waiting AW (possibly
        // this cycle's), or becomes an early burst.
        wire first_beat = w_hs && !in_burst;
        wire becomes_early = first_beat && aw_available == 0;
        wire wid_ok = rst || !w_hs
            || (in_burst ? wid == burst_id : (aw_available == 0 || wid == aw_front));
        wire early_wid_ok = rst || !aw_matches_early || awid == early_ids[early_head*I+:I];
        wire wid_capacity_ok = rst || (aw_available <= MAX_WRITES
            && early_count - aw_matches_early + becomes_early <= MAX_EARLY_BEATS);
        always @(*) begin
            `AXI3_MANAGER_RULE(aw_lock_ok);
            `AXI3_MANAGER_RULE(ar_lock_ok);
            `AXI3_MANAGER_RULE(wid_ok);
            `AXI3_MANAGER_RULE(early_wid_ok);
            `AXI3_MANAGER_RULE(wid_capacity_ok);
        end
        function automatic [7:0] wrap(input [7:0] index, input integer size);
            wrap = index >= size ? index - size : index;
        endfunction
        always @(posedge clk) begin
            if (rst) begin
                aw_head <= 0; aw_count <= 0; early_head <= 0; early_count <= 0; in_burst <= 1'b0;
            end else begin : track
                reg [7:0] ah, ac, eh, ec;
                ah = aw_head; ac = aw_count; eh = early_head; ec = early_count;
                if (aw_matches_early) begin
                    eh = wrap(eh + 1, MAX_EARLY_BEATS); ec = ec - 1;
                end else if (aw_waits && ac < MAX_WRITES) begin
                    aw_ids[wrap(ah + ac, MAX_WRITES)*I+:I] <= awid; ac = ac + 1;
                end
                if (first_beat) begin
                    if (ac != 0) begin
                        ah = wrap(ah + 1, MAX_WRITES); ac = ac - 1;
                    end else if (ec < MAX_EARLY_BEATS) begin
                        early_ids[wrap(eh + ec, MAX_EARLY_BEATS)*I+:I] <= wid; ec = ec + 1;
                    end
                end
                if (w_hs) begin
                    in_burst <= !wlast;
                    if (!in_burst) burst_id <= wid;
                end
                aw_head <= ah; aw_count <= ac; early_head <= eh; early_count <= ec;
            end
        end
    end endgenerate
endmodule
