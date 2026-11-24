; =============================================================================
; pcm_proto.s - $4011 PCM 再生の試作（音だけ。映像なし）
;
;   CPU が 81 サイクルごとに 1 回、ROM の 7bit の値を $4011（DMC の出力レベル）へ書く。
;   1789773 / 81 = 約 22,096Hz。どの道を通っても 1 周がちょうど 81 サイクルになるように、
;   NOP で長さをそろえてある（ページの切り替え・バンクの切り替えのときも同じ）。
;
;   ROM: BNROM（mapper 34）。各バンクの $8000-$EFFF（28KB = 112 ページ）が音声、
;        $F000-$FFFF にこのコード（全バンク共通）。最後のバンクの次は最初のバンクに戻る（繰り返し再生）。
;   出力: nes\cfg\video_code.cfg で $F000 からの 4KB。tools\video\make_pcm_proto.py が ROM にする。
; =============================================================================
.include "nes.inc"

.ifndef CYC
CYC = 81                ; 1 サンプルのサイクル数（ca65 -D CYC=96 で変更。81 = 22,096Hz / 96 = 18,644Hz）
.endif
DMC_RAW    = $4011
APU_STATUS = $4015
DATA_END   = $F0        ; 音声は $8000-$EFFF

.segment "ZEROPAGE"
ptr:    .res 2          ; 読んでいるページ（下位は常に 0 なので (ptr),y でページをまたがない）
bank:   .res 1

; ---- n サイクル待つ（n >= 2） --------------------------------------------------
; n には式（CYC - 16 など）が来るので、使う所はすべて (n) とかっこで囲む
.macro delay n
  .if (n) < 2
    .error "delay は 2 サイクル以上"
  .endif
  .if (n) >= 13
    ; 長い待ちはループで（ldx 2 + (dex 2 + bne 3) * k - 1 = 5k + 1。X を壊す）。残りの 2〜6 サイクルは下で
    ldx #((n) - 3) / 5
    .local L
L:  dex
    bne L
    delay ((n) - 1 - 5 * (((n) - 3) / 5))
  .elseif ((n) .mod 2) = 1
    bit $00             ; 3 サイクル（ゼロページ）
    .repeat ((n) - 3) / 2
      nop
    .endrepeat
  .else
    .repeat (n) / 2
      nop
    .endrepeat
  .endif
.endmacro

.segment "CODE"
; ここ（$F000）から 256 バイト以内に置くので、分岐がページをまたいで 1 サイクル増えることはない
play_loop:                  ; 左の数字は、ここに来た時点までのサイクル数
    lda (ptr),y             ; 5
    sta DMC_RAW             ; 9   この書き込みの間隔が 81 サイクル
    iny                     ; 11
    beq play_page               ; 13（ページの終わりなら 14）
    delay CYC - 16          ; 78（CYC = 81 のとき。以下同じ）
    jmp play_loop           ; 81
play_page:                      ; 14
    inc ptr+1               ; 19
    lda ptr+1               ; 22
    cmp #DATA_END           ; 24
    beq play_bank               ; 26（バンクの終わりなら 27）
    delay CYC - 29          ; 78
    jmp play_loop           ; 81
play_bank:                      ; 27
    lda #$80                ; 29
    sta ptr+1               ; 32
    ldx bank                ; 35
    inx                     ; 37
    cpx nbanks              ; 41
    bne play_sw                 ; 44（最後のバンクのあとは 43 → 下の ldx で 45。繰り返しの継ぎ目だけ 1 サイクル長い）
    ldx #0                  ; 45
play_sw:
    stx bank                ; 47
    lda banktable,x         ; 51（BNROM のバス衝突を避けるため、同じ値が入っている所へ書く）
    sta banktable,x         ; 56   ここでバンクが変わる（コードは全バンク同じなので、そのまま続く）
    delay CYC - 59          ; 78
    jmp play_loop           ; 81
play_loop_end:
.assert >play_loop = >(play_loop_end - 1), error, "再生ループが 1 ページに収まっていません"

reset:
    sei
    cld
    ldx #$40
    stx JOY2_FRAME          ; フレームカウンタの IRQ を止める
    ldx #$FF
    txs
    inx
    stx PPUCTRL             ; NMI なし（割り込みでタイミングが崩れないように）
    stx PPUMASK             ; 画面は表示しない
    stx APU_DMC             ; DMC の IRQ・再生なし（DMA で CPU が止まらないように）
    stx APU_STATUS
    bit PPUSTATUS
@vb1:
    bit PPUSTATUS
    bpl @vb1
@vb2:
    bit PPUSTATUS
    bpl @vb2
    ; 背景色を黒に（表示オフのときに見える色）
    lda #$3F
    sta PPUADDR
    lda #$00
    sta PPUADDR
    lda #$0F
    sta PPUDATA
    lda #$00
    sta PPUADDR
    sta PPUADDR

    lda #0
    sta bank
    tax
    lda banktable,x
    sta banktable,x         ; バンク 0
    lda #$00
    sta ptr
    lda #$80
    sta ptr+1
    ldy #0
    jmp play_loop

nmi:
irq:
    rti

.segment "RODATA"
nbanks: .byte 1             ; バンクの数（make_pcm_proto.py が書き換える）

.segment "BANKTABLE"
banktable:
.repeat 256, i
    .byte i
.endrepeat

.segment "VECTORS"
    .word nmi, reset, irq
