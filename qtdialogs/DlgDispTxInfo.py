################################################################################
#                                                                              #
# Copyright (C) 2011-2021, Armory Technologies, Inc.                           #
# Distributed under the GNU Affero General Public License (AGPL v3)            #
# See LICENSE or http://www.gnu.org/licenses/agpl.html                         #
#                                                                              #
################################################################################

from qtdialogs.qtdefines import ArmoryDialog

################################################################################
class DlgDispTxInfo(ArmoryDialog):
   def __init__(self, pytx, wlt, parent, main, mode=None, \
                             precomputeIdxGray=None, precomputeAmt=None, txtime=None):
      """
      This got freakin' complicated, because I'm trying to handle
      wallet/nowallet, BDM/noBDM and Std/Adv/Dev all at once.

      We can override the user mode as an input argument, in case a std
      user decides they want to see the tx in adv/dev mode
      """
      super(DlgDispTxInfo, self).__init__(parent, main)
      self.mode = mode


      FIELDS = enum('Hash', 'OutList', 'SumOut', 'InList', 'SumIn', \
                    'Time', 'Blk', 'Idx', 'TxSize', 'TxWeight')
      self.data = extractTxInfo(pytx, txtime)

      # If this is actually a ustx in here...
      ustx = None
      if isinstance(pytx, UnsignedTransaction):
         ustx = pytx
         pytx = ustx.getPyTxSignedIfPossible()


      self.pytx = pytx.copy()

      if self.mode == None:
         self.mode = self.main.usermode

      txHash = self.data[FIELDS.Hash]

      haveWallet = (wlt != None)
      haveBDM = TheBDM.getState() == BDM_BLOCKCHAIN_READY

      # Should try to identify what is change and what's not
      fee = None
      txAmt = self.data[FIELDS.SumOut]

      # Collect our own outputs only, and ID non-std tx
      svPairSelf = []
      svPairOther = []
      indicesSelf = []
      indicesOther = []
      indicesMakeGray = []
      idx = 0
      for scrType, amt, script, msInfo in self.data[FIELDS.OutList]:
         # If it's a multisig, pretend it's P2SH
         if scrType == CPP_TXOUT_MULTISIG:
            script = script_to_p2sh_script(script)
            scrType = CPP_TXOUT_P2SH

         if scrType in CPP_TXOUT_HAS_ADDRSTR:
            try:
               addrStr = script_to_addrStr(script)
               addr160 = addrStr_to_hash160(addrStr)[1]
            except:
               addr160 = ""

            scrAddr = script_to_scrAddr(script)
            if haveWallet and wlt.hasAddr160(addr160):
               svPairSelf.append([scrAddr, amt])
               indicesSelf.append(idx)
            else:
               svPairOther.append([scrAddr, amt])
               indicesOther.append(idx)
         else:
            indicesOther.append(idx)
         idx += 1

      txdir = None
      changeIndex = None
      svPairDisp = None

      if haveBDM and haveWallet and self.data[FIELDS.SumOut] and self.data[FIELDS.SumIn]:
         fee = self.data[FIELDS.SumOut] - self.data[FIELDS.SumIn]
         try:
            le = wlt.getLedgerEntryForTxHash(txHash)
            txAmt = le.getValue()

            if le.isSentToSelf():
               txdir = self.tr('Sent-to-Self')
               svPairDisp = []
               if len(self.pytx.outputs)==1:
                  txAmt = fee
                  triplet = self.data[FIELDS.OutList][0]
                  scrAddr = script_to_scrAddr(triplet[2])
                  svPairDisp.append([scrAddr, triplet[1]])
               else:
                  txAmt, changeIndex = determineSentToSelfAmt(le, wlt)
                  for i, triplet in enumerate(self.data[FIELDS.OutList]):
                     if not i == changeIndex:
                        scrAddr = script_to_scrAddr(triplet[2])
                        svPairDisp.append([scrAddr, triplet[1]])
                     else:
                        indicesMakeGray.append(i)
            else:
               if le.getValue() > 0:
                  txdir = self.tr('Received')
                  svPairDisp = svPairSelf
                  indicesMakeGray.extend(indicesOther)
               if le.getValue() < 0:
                  txdir = self.tr('Sent')
                  svPairDisp = svPairOther
                  indicesMakeGray.extend(indicesSelf)
         except:
            pass


      # If this is a USTX, the above calculation probably didn't do its job
      # It is possible, but it's also possible that this Tx has nothing to
      # do with our wallet, which is not the focus of the above loop/conditions
      # So we choose to pass in the amount we already computed based on extra
      # information available in the USTX structure
      if precomputeAmt:
         txAmt = precomputeAmt


      layout = QGridLayout()
      lblDescr = QLabel(self.tr('Transaction Information:'))

      layout.addWidget(lblDescr, 0, 0, 1, 1)

      frm = QFrame()
      frm.setFrameStyle(STYLE_RAISED)
      frmLayout = QGridLayout()
      lbls = []



      # Show the transaction ID, with the user's preferred endianness
      # I hate BE, but block-explorer requires it so it's probably a better default
      endianness = self.main.getSettingOrSetDefault('PrefEndian', BIGENDIAN)
      estr = ''
      if self.mode in (USERMODE.Advanced, USERMODE.Expert):
         estr = ' (BE)' if endianness == BIGENDIAN else ' (LE)'

      lbls.append([])
      lbls[-1].append(self.main.createToolTipWidget(self.tr('Unique identifier for this transaction')))
      lbls[-1].append(QLabel(self.tr('Transaction ID' )+ estr + ':'))


      # Want to display the hash of the Tx if we have a valid one:
      # A USTX does not have a valid hash until it's completely signed, though
      longTxt = self.tr('[[ Transaction ID cannot be determined without all signatures ]]')
      w, h = relaxedSizeStr(QRichLabel(''), longTxt)

      tempPyTx = self.pytx.copy()
      if ustx:
         finalTx = ustx.getBroadcastTxIfReady()
         if finalTx:
            tempPyTx = finalTx.copy()
         else:
            tempPyTx = None
            lbls[-1].append(QRichLabel(self.tr('<font color="gray"> '
               '[[ Transaction ID cannot be determined without all signatures ]] '
               '</font>')))

      if tempPyTx:
         txHash = binary_to_hex(tempPyTx.getHash(), endOut=endianness)
         lbls[-1].append(QLabel(txHash))


      lbls[-1][-1].setMinimumWidth(w)

      if self.mode in (USERMODE.Expert,):
         # Add protocol version and locktime to the display
         lbls.append([])
         lbls[-1].append(self.main.createToolTipWidget(self.tr('Bitcoin Protocol Version Number')))
         lbls[-1].append(QLabel(self.tr('Tx Version:')))
         lbls[-1].append(QLabel(str(self.pytx.version)))

         lbls.append([])
         lbls[-1].append(self.main.createToolTipWidget(
            self.tr('The time at which this transaction becomes valid.')))
         lbls[-1].append(QLabel(self.tr('Lock-Time:')))
         if self.pytx.lockTime == 0:
            lbls[-1].append(QLabel(self.tr('Immediate (0)')))
         elif self.pytx.lockTime < 500000000:
            lbls[-1].append(QLabel(self.tr('Block %s' % self.pytx.lockTime)))
         else:
            lbls[-1].append(QLabel(unixTimeToFormatStr(self.pytx.lockTime)))



      lbls.append([])
      lbls[-1].append(self.main.createToolTipWidget(self.tr('Comment stored for this transaction in this wallet')))
      lbls[-1].append(QLabel(self.tr('User Comment:')))
      try:
         txhash_bin = hex_to_binary(txHash, endOut=endianness)
      except:
         txhash_bin = txHash
      comment_tx = ''
      if haveWallet:
         comment_tx = wlt.getComment(txhash_bin)
         if not comment_tx: # and tempPyTx:
            comment_tx = wlt.getAddrCommentIfAvail(txhash_bin)
            #for txout in tempPyTx.outputs:
             #  script = script_to_scrAddr(txout.getScript())


      if comment_tx:
         lbls[-1].append(QRichLabel(comment_tx))
      else:
         lbls[-1].append(QRichLabel(self.tr('<font color="gray">[None]</font>')))


      if not self.data[FIELDS.Time] == None:
         lbls.append([])
         if self.data[FIELDS.Blk] >= 2 ** 32 - 1:
            lbls[-1].append(self.main.createToolTipWidget(
                  self.tr('The time that you computer first saw this transaction')))
         else:
            lbls[-1].append(self.main.createToolTipWidget(
                  self.tr('All transactions are eventually included in a "block."  The '
                  'time shown here is the time that the block entered the "blockchain."')))
         lbls[-1].append(QLabel('Transaction Time:'))
         lbls[-1].append(QLabel(str(self.data[FIELDS.Time])))

      if not self.data[FIELDS.Blk] == None:
         nConf = 0
         if self.data[FIELDS.Blk] >= 2 ** 32 - 1:
            lbls.append([])
            lbls[-1].append(self.main.createToolTipWidget(
                  self.tr('This transaction has not yet been included in a block. '
                  'It usually takes 5-20 minutes for a transaction to get '
                  'included in a block after the user hits the "Send" button.')))
            lbls[-1].append(QLabel('Block Number:'))
            lbls[-1].append(QRichLabel('<i>Not in the blockchain yet</i>'))
         else:
            idxStr = ''
            if not self.data[FIELDS.Idx] == None and self.mode == USERMODE.Expert:
               idxStr ='  (Tx #%d)' % self.data[FIELDS.Idx]
            lbls.append([])
            lbls[-1].append(self.main.createToolTipWidget(
                  self.tr('Every transaction is eventually included in a "block" which '
                  'is where the transaction is permanently recorded.  A new block '
                  'is produced approximately every 10 minutes.')))
            lbls[-1].append(QLabel('Included in Block:'))
            lbls[-1].append(QRichLabel(str(self.data[FIELDS.Blk]) + idxStr))
            if TheBDM.getState() == BDM_BLOCKCHAIN_READY:
               nConf = TheBDM.getTopBlockHeight() - self.data[FIELDS.Blk] + 1
               lbls.append([])
               lbls[-1].append(self.main.createToolTipWidget(
                     self.tr('The number of blocks that have been produced since '
                     'this transaction entered the blockchain.  A transaction '
                     'with 6 or more confirmations is nearly impossible to reverse.')))
               lbls[-1].append(QLabel(self.tr('Confirmations:')))
               lbls[-1].append(QRichLabel(str(nConf)))

      isRBF = self.pytx.isRBF()
      if isRBF:
         lbls.append([])
         lbls[-1].append(self.main.createToolTipWidget(
               self.tr('This transaction can be replaced by another transaction that '
               'spends the same inputs if the replacement transaction has '
               'a higher fee.')))
         lbls[-1].append(QLabel(self.tr('Mempool Replaceable: ')))
         lbls[-1].append(QRichLabel(str(isRBF)))




      if svPairDisp == None and precomputeAmt == None:
         # Couldn't determine recip/change outputs
         lbls.append([])
         lbls[-1].append(self.main.createToolTipWidget(
               self.tr('Most transactions have at least a recipient output and a '
               'returned-change output.  You do not have enough information '
               'to determine which is which, and so this fields shows the sum '
               'of <b>all</b> outputs.')))
         lbls[-1].append(QLabel(self.tr('Sum of Outputs:')))
         lbls[-1].append(QLabel(coin2str(txAmt, maxZeros=1).strip() + '  BTC'))
      else:
         lbls.append([])
         lbls[-1].append(self.main.createToolTipWidget(
               self.tr('Bitcoins were either sent or received, or sent-to-self')))
         lbls[-1].append(QLabel('Transaction Direction:'))
         lbls[-1].append(QRichLabel(txdir))

         lbls.append([])
         lbls[-1].append(self.main.createToolTipWidget(
               self.tr('The value shown here is the net effect on your '
               'wallet, including transaction fee.')))
         lbls[-1].append(QLabel('Transaction Amount:'))
         lbls[-1].append(QRichLabel(coin2str(txAmt, maxZeros=1).strip() + '  BTC'))
         if txAmt < 0:
            lbls[-1][-1].setText('<font color="red">' + lbls[-1][-1].text() + '</font> ')
         elif txAmt > 0:
            lbls[-1][-1].setText('<font color="green">' + lbls[-1][-1].text() + '</font> ')


      if not self.data[FIELDS.TxSize] == None:
         txsize = str(self.data[FIELDS.TxSize])
         txsize_str = self.tr("%s bytes" % txsize)
         lbls.append([])
         lbls[-1].append(self.main.createToolTipWidget(
            self.tr('Size of the transaction in bytes')))
         lbls[-1].append(QLabel(self.tr('Tx Size: ')))
         lbls[-1].append(QLabel(txsize_str))

      if not self.data[FIELDS.SumIn] == None:
         fee = self.data[FIELDS.SumIn] - self.data[FIELDS.SumOut]
         lbls.append([])
         lbls[-1].append(self.main.createToolTipWidget(
            self.tr('Transaction fees go to users supplying the Bitcoin network with '
            'computing power for processing transactions and maintaining security.')))
         lbls[-1].append(QLabel('Tx Fee Paid:'))

         fee_str = coin2str(fee, maxZeros=0).strip() + '  BTC'
         if not self.data[FIELDS.TxWeight] == None:
            fee_byte = float(fee) / float(self.data[FIELDS.TxWeight])
            fee_str += ' (%d sat/B)' % fee_byte

         lbls[-1].append(QLabel(fee_str))





      lastRow = 0
      for row, lbl3 in enumerate(lbls):
         lastRow = row
         for i in range(3):
            lbl3[i].setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            lbl3[i].setTextInteractionFlags(Qt.TextSelectableByMouse | \
                                            Qt.TextSelectableByKeyboard)
         frmLayout.addWidget(lbl3[0], row, 0, 1, 1)
         frmLayout.addWidget(lbl3[1], row, 1, 1, 1)
         frmLayout.addWidget(lbl3[2], row, 3, 1, 2)

      spacer = QSpacerItem(20, 20)
      frmLayout.addItem(spacer, 0, 2, len(lbls), 1)

      # Show the list of recipients, if possible
      numShow = 3
      rlbls = []
      if svPairDisp is not None:
         numRV = len(svPairDisp)
         for i, sv in enumerate(svPairDisp):
            rlbls.append([])
            if i == 0:
               rlbls[-1].append(self.main.createToolTipWidget(
                  self.tr('All outputs of the transaction <b>excluding</b> change-'
                  'back-to-sender outputs.  If this list does not look '
                  'correct, it is possible that the change-output was '
                  'detected incorrectly -- please check the complete '
                  'input/output list below.')))
               rlbls[-1].append(QLabel(self.tr('Recipients:')))
            else:
               rlbls[-1].extend([QLabel(), QLabel()])

            rlbls[-1].append(QLabel(scrAddr_to_addrStr(sv[0])))
            if numRV > 1:
               rlbls[-1].append(QLabel(coin2str(sv[1], maxZeros=1) + '  BTC'))
            else:
               rlbls[-1].append(QLabel(''))
            ffixBold = GETFONT('Fixed', 10)
            ffixBold.setWeight(QFont.Bold)
            rlbls[-1][-1].setFont(ffixBold)

            if numRV > numShow and i == numShow - 2:
               moreStr = self.tr('[%s more recipients]' % (numRV - numShow + 1))
               rlbls.append([])
               rlbls[-1].extend([QLabel(), QLabel(), QLabel(moreStr), QLabel()])
               break


         # ##
         for i, lbl4 in enumerate(rlbls):
            for j in range(4):
               lbl4[j].setTextInteractionFlags(Qt.TextSelectableByMouse | \
                                            Qt.TextSelectableByKeyboard)
            row = lastRow + 1 + i
            frmLayout.addWidget(lbl4[0], row, 0, 1, 1)
            frmLayout.addWidget(lbl4[1], row, 1, 1, 1)
            frmLayout.addWidget(lbl4[2], row, 3, 1, 1)
            frmLayout.addWidget(lbl4[3], row, 4, 1, 1)



      # TxIns/Senders
      wWlt = relaxedSizeStr(GETFONT('Var'), 'A' * 10)[0]
      wAddr = relaxedSizeStr(GETFONT('Var'), 'A' * 31)[0]
      wAmt = relaxedSizeStr(GETFONT('Fixed'), 'A' * 20)[0]
      if ustx:
         self.txInModel = TxInDispModel(ustx, self.data[FIELDS.InList], self.main)
      else:
         self.txInModel = TxInDispModel(pytx, self.data[FIELDS.InList], self.main)
      self.txInView = QTableView()
      self.txInView.setModel(self.txInModel)
      self.txInView.setSelectionBehavior(QTableView.SelectRows)
      self.txInView.setSelectionMode(QTableView.SingleSelection)
      self.txInView.horizontalHeader().setStretchLastSection(True)
      self.txInView.verticalHeader().setDefaultSectionSize(20)
      self.txInView.verticalHeader().hide()
      w, h = tightSizeNChar(self.txInView, 1)
      self.txInView.setMinimumHeight(2 * (1.4 * h))
      #self.txInView.setMaximumHeight(5 * (1.4 * h))
      self.txInView.hideColumn(TXINCOLS.OutPt)
      self.txInView.hideColumn(TXINCOLS.OutIdx)
      self.txInView.hideColumn(TXINCOLS.Script)
      self.txInView.hideColumn(TXINCOLS.AddrStr)

      if self.mode == USERMODE.Standard:
         initialColResize(self.txInView, [wWlt, wAddr, wAmt, 0, 0, 0, 0, 0, 0])
         self.txInView.hideColumn(TXINCOLS.FromBlk)
         self.txInView.hideColumn(TXINCOLS.ScrType)
         self.txInView.hideColumn(TXINCOLS.Sequence)
         # self.txInView.setSelectionMode(QTableView.NoSelection)
      elif self.mode == USERMODE.Advanced:
         initialColResize(self.txInView, [0.8 * wWlt, 0.6 * wAddr, wAmt, 0, 0, 0, 0.2, 0, 0])
         self.txInView.hideColumn(TXINCOLS.FromBlk)
         self.txInView.hideColumn(TXINCOLS.Sequence)
         # self.txInView.setSelectionMode(QTableView.NoSelection)
      elif self.mode == USERMODE.Expert:
         self.txInView.resizeColumnsToContents()

      self.txInView.setContextMenuPolicy(Qt.CustomContextMenu)
      self.txInView.customContextMenuRequested.connect(self.showContextMenuTxIn)

      # List of TxOuts/Recipients
      if not precomputeIdxGray is None:
         indicesMakeGray = precomputeIdxGray[:]
      self.txOutModel = TxOutDispModel(self.pytx, self.main, idxGray=indicesMakeGray)
      self.txOutView = QTableView()
      self.txOutView.setModel(self.txOutModel)
      self.txOutView.setSelectionBehavior(QTableView.SelectRows)
      self.txOutView.setSelectionMode(QTableView.SingleSelection)
      self.txOutView.verticalHeader().setDefaultSectionSize(20)
      self.txOutView.verticalHeader().hide()
      self.txOutView.setMinimumHeight(2 * (1.3 * h))
      #self.txOutView.setMaximumHeight(5 * (1.3 * h))
      initialColResize(self.txOutView, [wWlt, 0.8 * wAddr, wAmt, 0.25, 0])
      self.txOutView.hideColumn(TXOUTCOLS.Script)
      self.txOutView.hideColumn(TXOUTCOLS.AddrStr)
      if self.mode == USERMODE.Standard:
         self.txOutView.hideColumn(TXOUTCOLS.ScrType)
         initialColResize(self.txOutView, [wWlt, wAddr, 0.25, 0, 0])
         self.txOutView.horizontalHeader().setStretchLastSection(True)
         # self.txOutView.setSelectionMode(QTableView.NoSelection)
      elif self.mode == USERMODE.Advanced:
         initialColResize(self.txOutView, [0.8 * wWlt, 0.6 * wAddr, wAmt, 0.25, 0])
         # self.txOutView.setSelectionMode(QTableView.NoSelection)
      elif self.mode == USERMODE.Expert:
         initialColResize(self.txOutView, [wWlt, wAddr, wAmt, 0.25, 0])
      # self.txOutView.resizeColumnsToContents()

      self.txOutView.setContextMenuPolicy(Qt.CustomContextMenu)
      self.txOutView.customContextMenuRequested.connect(self.showContextMenuTxOut)

      self.lblTxioInfo = QRichLabel('')
      self.lblTxioInfo.setMinimumWidth(tightSizeNChar(self.lblTxioInfo, 30)[0])
      #self.txInView.clicked.connect(lambda: self.dispTxioInfo('In'))
      #self.txOutView.clicked.connect(lambda: self.dispTxioInfo('Out'))
      self.txInView.doubleClicked.connect(self.showTxInDialog)
      self.txOutView.doubleClicked.connect(self.showTxOutDialog)

      # scrFrm = QFrame()
      # scrFrm.setFrameStyle(STYLE_SUNKEN)
      # scrFrmLayout = Q


      self.scriptArea = QScrollArea()
      self.scriptArea.setWidget(self.lblTxioInfo)
      self.scriptFrm = makeLayoutFrame(HORIZONTAL, [self.scriptArea])
      # self.scriptFrm.setMaximumWidth(150)
      self.scriptArea.setMaximumWidth(200)

      self.frmIOList = QFrame()
      self.frmIOList.setFrameStyle(STYLE_SUNKEN)
      frmIOListLayout = QGridLayout()

      lblInputs = QLabel(self.tr('Transaction Inputs (Sending addresses):'))
      ttipText = (self.tr('All transactions require previous transaction outputs as inputs.'))
      if not haveBDM:
         ttipText += (self.tr('<b>Since the blockchain is not available, not all input '
                      'information is available</b>.  You need to view this '
                      'transaction on a system with an internet connection '
                      '(and blockchain) if you want to see the complete information.'))
      else:
         ttipText += (self.tr('Each input is like an X amount dollar bill.  Usually there are more inputs '
                      'than necessary for the transaction, and there will be an extra '
                      'output returning change to the sender'))
      ttipInputs = self.main.createToolTipWidget(ttipText)

      lblOutputs = QLabel(self.tr('Transaction Outputs (Receiving addresses):'))
      ttipOutputs = self.main.createToolTipWidget(
                  self.tr('Shows <b>all</b> outputs, including other recipients '
                  'of the same transaction, and change-back-to-sender outputs '
                  '(change outputs are displayed in light gray).'))

      self.lblChangeDescr = QRichLabel( self.tr('Some outputs might be "change."'), doWrap=False)
      self.lblChangeDescr.setOpenExternalLinks(True)



      inStrip = makeLayoutFrame(HORIZONTAL, [lblInputs, ttipInputs, STRETCH])
      outStrip = makeLayoutFrame(HORIZONTAL, [lblOutputs, ttipOutputs, STRETCH])

      frmIOListLayout.addWidget(inStrip, 0, 0, 1, 1)
      frmIOListLayout.addWidget(self.txInView, 1, 0, 1, 1)
      frmIOListLayout.addWidget(outStrip, 2, 0, 1, 1)
      frmIOListLayout.addWidget(self.txOutView, 3, 0, 1, 1)
      # frmIOListLayout.addWidget(self.lblTxioInfo, 0,1, 4,1)
      self.frmIOList.setLayout(frmIOListLayout)


      self.btnIOList = QPushButton('')
      self.btnCopy = QPushButton(self.tr('Copy Raw Tx (Hex)'))
      self.lblCopied = QRichLabel('')
      self.btnOk = QPushButton(self.tr('OK'))
      self.btnIOList.setCheckable(True)
      self.btnIOList.clicked.connect(self.extraInfoClicked)
      self.btnOk.clicked.connect(self.accept)
      self.btnCopy.clicked.connect(self.copyRawTx)

      btnStrip = makeHorizFrame([self.btnIOList,
                                 self.btnCopy,
                                 self.lblCopied,
                                 'Stretch',
                                 self.lblChangeDescr,
                                 'Stretch',
                                 self.btnOk])

      if not self.mode == USERMODE.Expert:
         self.btnCopy.setVisible(False)


      if self.mode == USERMODE.Standard:
         self.btnIOList.setChecked(False)
      else:
         self.btnIOList.setChecked(True)
      self.extraInfoClicked()


      frm.setLayout(frmLayout)
      layout.addWidget(frm, 2, 0, 1, 1)
      layout.addWidget(self.scriptArea, 2, 1, 1, 1)
      layout.addWidget(self.frmIOList, 3, 0, 1, 2)
      layout.addWidget(btnStrip, 4, 0, 1, 2)

      # bbox = QDialogButtonBox(QDialogButtonBox.Ok)
      # self.connect(bbox, SIGNAL('accepted()'), self.accept)
      # layout.addWidget(bbox, 6,0, 1,1)

      self.setLayout(layout)
      #self.layout().setSizeConstraint(QLayout.SetFixedSize)
      self.setWindowTitle(self.tr('Transaction Info'))



   def extraInfoClicked(self):
      if self.btnIOList.isChecked():
         self.frmIOList.setVisible(True)
         self.btnCopy.setVisible(True)
         self.lblCopied.setVisible(True)
         self.btnIOList.setText(self.tr('<<< Less Info'))
         self.lblChangeDescr.setVisible(True)
         self.scriptArea.setVisible(False) # self.mode == USERMODE.Expert)
         # Disabling script area now that you can double-click to get it
      else:
         self.frmIOList.setVisible(False)
         self.scriptArea.setVisible(False)
         self.btnCopy.setVisible(False)
         self.lblCopied.setVisible(False)
         self.lblChangeDescr.setVisible(False)
         self.btnIOList.setText(self.tr('Advanced >>>'))

   def dispTxioInfo(self, InOrOut):
      hexScript = None
      headStr = None
      if InOrOut == 'In':
         selection = self.txInView.selectedIndexes()
         if len(selection) == 0:
            return
         row = selection[0].row()
         hexScript = str(self.txInView.model().index(row, TXINCOLS.Script).data().toString())
         headStr = self.tr('TxIn Script:')
      elif InOrOut == 'Out':
         selection = self.txOutView.selectedIndexes()
         if len(selection) == 0:
            return
         row = selection[0].row()
         hexScript = str(self.txOutView.model().index(row, TXOUTCOLS.Script).data().toString())
         headStr = self.tr('TxOut Script:')


      if hexScript:
         binScript = hex_to_binary(hexScript)
         addrStr = None
         scrType = getTxOutScriptType(binScript)
         if scrType in CPP_TXOUT_HAS_ADDRSTR:
            addrStr = script_to_addrStr(binScript)

         oplist = convertScriptToOpStrings(hex_to_binary(hexScript))
         opprint = []
         prevOpIsPushData = False
         for op in oplist:

            if addrStr is None or not prevOpIsPushData:
               opprint.append(op)
            else:
               opprint.append(op + ' <font color="gray">(%s)</font>' % addrStr)
               prevOpIsPushData = False

            if 'pushdata' in op.lower():
               prevOpIsPushData = True

         lblScript = QRichLabel('')
         lblScript.setText('<b>Script:</b><br><br>' + '<br>'.join(opprint))
         lblScript.setWordWrap(False)
         lblScript.setTextInteractionFlags(Qt.TextSelectableByMouse | \
                                        Qt.TextSelectableByKeyboard)

         self.scriptArea.setWidget(makeLayoutFrame(VERTICAL, [lblScript]))
         self.scriptArea.setMaximumWidth(200)


   def copyRawTx(self):
      clipb = QApplication.clipboard()
      clipb.clear()
      #print "Binscript: " + binary_to_hex(self.pytx.inputs[0].binScript)
      clipb.setText(binary_to_hex(self.pytx.serialize()))
      self.lblCopied.setText(self.tr('<i>Copied to Clipboard!</i>'))


   #############################################################################
   def showTxInDialog(self, *args):
      # I really should've just used a dictionary instead of list with enum indices
      FIELDS = enum('Hash', 'OutList', 'SumOut', 'InList', 'SumIn', 'Time', 'Blk', 'Idx')
      try:
         idx = self.txInView.selectedIndexes()[0].row()
         DlgDisplayTxIn(self, self.main, self.pytx, idx, self.data[FIELDS.InList]).exec_()
      except:
         LOGEXCEPT('Error showing TxIn')

   #############################################################################
   def showTxOutDialog(self, *args):
      # I really should've just used a dictionary instead of list with enum indices
      FIELDS = enum('Hash', 'OutList', 'SumOut', 'InList', 'SumIn', 'Time', 'Blk', 'Idx')
      try:
         idx = self.txOutView.selectedIndexes()[0].row()
         DlgDisplayTxOut(self, self.main, self.pytx, idx).exec_()
      except:
         LOGEXCEPT('Error showing TxOut')

   #############################################################################
   def showContextMenuTxIn(self, pos):
      menu = QMenu(self.txInView)
      std = (self.main.usermode == USERMODE.Standard)
      adv = (self.main.usermode == USERMODE.Advanced)
      dev = (self.main.usermode == USERMODE.Expert)

      if True:   actCopySender = menu.addAction(self.tr("Copy Sender Address"))
      if True:   actCopyWltID = menu.addAction(self.tr("Copy Wallet ID"))
      if True:   actCopyAmount = menu.addAction(self.tr("Copy Amount"))
      if True:   actMoreInfo = menu.addAction(self.tr("More Info"))
      idx = self.txInView.selectedIndexes()[0]
      action = menu.exec_(QCursor.pos())

      if action == actMoreInfo:
         self.showTxInDialog()
      if action == actCopyWltID:
         s = str(self.txInView.model().index(idx.row(), TXINCOLS.WltID).data().toString())
      elif action == actCopySender:
         s = str(self.txInView.model().index(idx.row(), TXINCOLS.Sender).data().toString())
      elif action == actCopyAmount:
         s = str(self.txInView.model().index(idx.row(), TXINCOLS.Btc).data().toString())
      #elif dev and action == actCopyOutPt:
         #s1 = str(self.txInView.model().index(idx.row(), TXINCOLS.OutPt).data().toString())
         #s2 = str(self.txInView.model().index(idx.row(), TXINCOLS.OutIdx).data().toString())
         #s = s1 + ':' + s2
      #elif dev and action == actCopyScript:
         #s = str(self.txInView.model().index(idx.row(), TXINCOLS.Script).data().toString())
      else:
         return

      clipb = QApplication.clipboard()
      clipb.clear()
      clipb.setText(s.strip())

   #############################################################################
   def showContextMenuTxOut(self, pos):
      menu = QMenu(self.txOutView)
      std = (self.main.usermode == USERMODE.Standard)
      adv = (self.main.usermode == USERMODE.Advanced)
      dev = (self.main.usermode == USERMODE.Expert)

      if True:   actCopyRecip  = menu.addAction(self.tr("Copy Recipient Address"))
      if True:   actCopyWltID  = menu.addAction(self.tr("Copy Wallet ID"))
      if True:   actCopyAmount = menu.addAction(self.tr("Copy Amount"))
      if dev:    actCopyScript = menu.addAction(self.tr("Copy Raw Script"))
      idx = self.txOutView.selectedIndexes()[0]
      action = menu.exec_(QCursor.pos())

      if action == actCopyWltID:
         s = self.txOutView.model().index(idx.row(), TXOUTCOLS.WltID).data().toString()
      elif action == actCopyRecip:
         s = self.txOutView.model().index(idx.row(), TXOUTCOLS.AddrStr).data().toString()
      elif action == actCopyAmount:
         s = self.txOutView.model().index(idx.row(), TXOUTCOLS.Btc).data().toString()
      elif dev and action == actCopyScript:
         s = self.txOutView.model().index(idx.row(), TXOUTCOLS.Script).data().toString()
      else:
         return

      clipb = QApplication.clipboard()
      clipb.clear()
      clipb.setText(str(s).strip())