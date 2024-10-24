////////////////////////////////////////////////////////////////////////////////
//                                                                            //
//  Copyright (C) 2016, goatpig                                               //
//  Distributed under the MIT license                                         //
//  See LICENSE-MIT or https://opensource.org/licenses/MIT                    //
//                                                                            //
////////////////////////////////////////////////////////////////////////////////

#ifndef _H_SCRIPT
#define _H_SCRIPT

//signer flags
#define SCRIPT_VERIFY_CHECKLOCKTIMEVERIFY 0x00000001
#define SCRIPT_VERIFY_CHECKSEQUENCEVERIFY 0x00000002
#define SCRIPT_VERIFY_P2SH                0x00000004
#define SCRIPT_VERIFY_P2SH_SHA256         0x00000008
#define SCRIPT_VERIFY_SEGWIT              0x00000010
#define P2SH_TIMESTAMP                    1333238400

#define STACKITEM_OPCODE_PREFIX           0x10
#define STACKITEM_PUSHDATA_PREFIX         0x11
#define STACKITEM_SERSCRIPT_PREFIX        0x12
#define STACKITEM_SIG_PREFIX              0x13
#define STACKITEM_MULTISIG_PREFIX         0x14

#include <algorithm>
#include "BinaryData.h"
#include "EncryptionUtils.h"
#include "BtcUtils.h"
#include "SigHashEnum.h"
#include "TxEvalState.h"
#include "ResolverFeed.h"

#include "protobuf/Signer.pb.h"


namespace Armory
{
   namespace Signer
   {
      class TransactionStub;
      class SigHashData;
      class SigHashDataSegWit;
      class SignerProxy;

      //////////////////////////////////////////////////////////////////////////
      class ScriptException : public std::runtime_error
      {
      public:
         ScriptException(const std::string& what) : std::runtime_error(what)
         {}
      };

      //////////////////////////////////////////////////////////////////////////
      struct OpCode
      {
         size_t offset_;
         uint8_t opcode_;
         BinaryDataRef dataRef_;

         virtual ~OpCode(void)
         {}
      };

      struct ReversedStackEntry;

      //////////////////////////////////////////////////////////////////////////
      struct ExtendedOpCode : public OpCode
      {
         unsigned itemIndex_;
         BinaryData data_;

         std::vector<std::shared_ptr<ReversedStackEntry>> referenceStackItemVec_;

         ExtendedOpCode(const OpCode& oc) :
            OpCode(oc)
         {}
      };

      //////////////////////////////////////////////////////////////////////////
      class ScriptParser
      {
      protected:

         bool notZero(const BinaryData& data)
         {
            //TODO: check for negative zero as well

            if (data.getSize() != 0)
            {
               auto ptr = data.getPtr();

               for (unsigned i = 0; i < data.getSize(); i++)
                  if (*(ptr++) != 0)
                     return true;
            }

            return false;
         }

         OpCode getNextOpcode(BinaryRefReader& brr) const;

         int64_t rawBinaryToInt(const BinaryData& bd)
         {
            auto len = bd.getSize();
            if (len == 0)
               return 0;

            if (len > 4)
               throw ScriptException("int overflow");

            int64_t val = 0;
            memcpy(&val, bd.getPtr(), len);

            auto valptr = (uint8_t*)&val;
            --len;
            if (valptr[len] & 0x80)
            {
               valptr[len] &= 0x7F;
               val *= -1;
            }

            return val;
         }

         BinaryData intToRawBinary(int64_t val)
         {
            //op_code outputs are allowed to overflow the 32 bit int limitation
            if (val == 0)
               return BinaryData();

            auto absval = abs(val);
            bool neg = val < 0;

            int mostSignificantByteOffset = 7;

            auto intptr = (uint8_t*)&absval;
            while (mostSignificantByteOffset > 0)
            {
               auto byteval = *(intptr + mostSignificantByteOffset);
               if (byteval > 0)
               {
                  if (byteval & 0x80)
                     ++mostSignificantByteOffset;
                  break;
               }

               --mostSignificantByteOffset;
            }

            if (mostSignificantByteOffset > 7)
               throw ScriptException("int overflow");

            if (neg)
            {
               intptr[mostSignificantByteOffset] |= 0x80;
            }

            ++mostSignificantByteOffset;
            BinaryData bd(mostSignificantByteOffset);
            auto ptr = bd.getPtr();
            memcpy(ptr, &absval, mostSignificantByteOffset);

            return bd;
         }

         void seekToEndIf(BinaryRefReader& brr)
         {
            while (brr.getSizeRemaining() > 0)
            {
               seekToNextIfSwitch(brr);
               auto opcode = brr.get_uint8_t();
               if (opcode == OP_ENDIF)
                  return;
            }

            throw ScriptException("couldn't not find ENDIF opcode");
         }

         void seekToNextIfSwitch(BinaryRefReader& brr)
         {
            int depth = 0;
            while (brr.getSizeRemaining() > 0)
            {
               auto&& data = getNextOpcode(brr);
               switch (data.opcode_)
               {
               case OP_IF:
               case OP_NOTIF:
                  depth++;
                  break;

               case OP_ENDIF:
                  if (depth-- > 0)
                     break;
                  [[fallthrough]];

               case OP_ELSE:
               {
                  if (depth > 0)
                     break;

                  brr.rewind(1 + data.dataRef_.getSize());
                  return;
               }
               }
            }

            throw ScriptException("no extra if switches");
         }

         virtual void processOpCode(const OpCode&) = 0;

      public:

         void parseScript(BinaryRefReader& brr);

         size_t seekToOpCode(BinaryRefReader&, OPCODETYPE) const;
      };

      //////////////////////////////////////////////////////////////////////////
      class StackInterpreter : public ScriptParser
      {
      private:
         std::vector<BinaryData> stack_;
         std::vector<BinaryData> altstack_;
         bool onlyPushDataInInput_ = true;

         const TransactionStub* txStubPtr_;
         const unsigned inputIndex_;

         bool isValid_ = false;
         unsigned opcount_ = 0;

         unsigned flags_;

         BinaryDataRef outputScriptRef_;
         BinaryData p2shScript_;

         std::shared_ptr<SigHashDataSegWit> SHD_SW_ = nullptr;

         TxInEvalState txInEvalState_;

      protected:
         std::shared_ptr<SigHashData> sigHashDataObject_ = nullptr;
         virtual SIGHASH_TYPE getSigHashSingleByte(uint8_t) const;

      private:
         void processOpCode(const OpCode&);

         void op_if(BinaryRefReader& brr, bool isOutputScript)
         {
            //find next if switch offset
            auto innerBlock = brr.fork();
            seekToNextIfSwitch(innerBlock);

            //get block ref for this if block
            BinaryRefReader thisIfBlock(
               brr.get_BinaryDataRef(innerBlock.getPosition()));

            try
            {
               //verify top stack item
               op_verify();

               //reset isValid flag
               isValid_ = false;

               //process block
               processScript(thisIfBlock, isOutputScript);

               //exit if statement
               seekToEndIf(brr);
            }
            catch (ScriptException&)
            {
               //move to next opcode
               auto opcode = brr.get_uint8_t();
               if (opcode == OP_ENDIF)
                  return;

               if (opcode != OP_ELSE)
                  throw ScriptException("expected OP_ELSE");

               //look for else or endif opcode
               innerBlock = brr.fork();
               seekToNextIfSwitch(innerBlock);

               thisIfBlock = BinaryRefReader(
                  brr.get_BinaryDataRef(innerBlock.getPosition()));

               //process block
               processScript(thisIfBlock, isOutputScript);

               //exit if statement
               seekToEndIf(brr);
            }
         }

         void op_0(void)
         {
            stack_.push_back(BinaryData());
         }

         void op_true(void)
         {
            BinaryData btrue;
            btrue.append(1);

            stack_.push_back(std::move(btrue));
         }

         void op_1negate(void)
         {
            stack_.push_back(std::move(intToRawBinary(-1)));
         }

         void op_depth(void)
         {
            BinaryWriter bw;
            stack_.push_back(std::move(intToRawBinary(stack_.size())));
         }

         void op_dup(void)
         {
            stack_.push_back(stack_back());
         }

         void op_nip(void)
         {
            auto&& data1 = pop_back();
            auto&& data2 = pop_back();

            stack_.push_back(std::move(data1));
         }

         void op_over(void)
         {
            if (stack_.size() < 2)
               throw ScriptException("stack is too small for op_over");

            auto stackIter = stack_.rbegin();
            auto data = *(stackIter + 1);
            stack_.push_back(std::move(data));
         }

         void op_2dup(void)
         {
            if (stack_.size() < 2)
               throw ScriptException("stack is too small for op_2dup");

            auto stackIter = stack_.rbegin();
            auto i0 = *(stackIter + 1);
            auto i1 = *stackIter;

            stack_.push_back(i0);
            stack_.push_back(i1);
         }

         void op_3dup(void)
         {
            if (stack_.size() < 3)
               throw ScriptException("stack is too small for op_3dup");

            auto stackIter = stack_.rbegin();
            auto i0 = *(stackIter + 2);
            auto i1 = *(stackIter + 1);
            auto i2 = *stackIter;

            stack_.push_back(i0);
            stack_.push_back(i1);
            stack_.push_back(i2);
         }

         void op_2over(void)
         {
            if (stack_.size() < 4)
               throw ScriptException("stack is too small for op_2over");

            auto stackIter = stack_.rbegin();
            auto i0 = *(stackIter + 3);
            auto i1 = *(stackIter + 2);

            stack_.push_back(i0);
            stack_.push_back(i1);
         }

         void op_toaltstack(void)
         {
            auto&& a = pop_back();
            altstack_.push_back(std::move(a));
         }

         void op_fromaltstack(void)
         {
            if (altstack_.size() == 0)
               throw ScriptException("tried to pop an empty altstack");

            auto& a = altstack_.back();
            stack_.push_back(a);
            altstack_.pop_back();
         }

         void op_ifdup(void)
         {
            auto& data = stack_back();
            if (notZero(data))
               stack_.push_back(data);
         }

         void op_pick(void)
         {
            auto&& a = pop_back();
            auto aI = rawBinaryToInt(a);

            if (aI >= (int64_t)stack_.size())
               throw ScriptException("op_pick index exceeds stack size");

            auto stackIter = stack_.rbegin() + aI;
            stack_.push_back(*stackIter);
         }

         void op_roll(void)
         {
            auto&& a = pop_back();
            auto rollindex = rawBinaryToInt(a);

            if (rollindex >= (int64_t)stack_.size())
               throw ScriptException("op_roll index exceeds stack size");

            std::vector<BinaryData> dataVec;
            while (rollindex-- > 0)
               dataVec.push_back(std::move(pop_back()));
            auto&& rolldata = pop_back();

            auto dataIter = dataVec.rbegin();
            while (dataIter != dataVec.rend())
            {
               stack_.push_back(std::move(*dataIter));
               ++dataIter;
            }

            stack_.push_back(rolldata);
         }

         void op_rot(void)
         {
            auto&& c = pop_back();
            auto&& b = pop_back();
            auto&& a = pop_back();

            stack_.push_back(std::move(b));
            stack_.push_back(std::move(c));
            stack_.push_back(std::move(a));
         }

         void op_swap(void)
         {
            auto&& data1 = pop_back();
            auto&& data2 = pop_back();

            stack_.push_back(std::move(data1));
            stack_.push_back(std::move(data2));
         }

         void op_tuck(void)
         {
            auto&& b = pop_back();
            auto&& a = pop_back();

            stack_.push_back(std::move(b));
            stack_.push_back(std::move(a));
            stack_.push_back(std::move(b));
         }

         void op_ripemd160(void)
         {
            auto&& data = pop_back();
            auto&& hash = BtcUtils().ripemd160(data);
            stack_.push_back(std::move(hash));
         }

         void op_sha256(void)
         {
            auto&& data = pop_back();
            auto&& sha256 = BtcUtils::getSha256(data);
            stack_.push_back(std::move(sha256));
         }

         void op_hash160()
         {
            auto&& data = pop_back();
            auto&& hash160 = BtcUtils::getHash160(data);
            stack_.push_back(std::move(hash160));
         }

         void op_hash256()
         {
            auto&& data = pop_back();
            auto&& hash256 = BtcUtils::getHash256(data);
            stack_.push_back(std::move(hash256));
         }

         void op_size(void)
         {
            auto& data = stack_back();
            stack_.push_back(std::move(intToRawBinary(data.getSize())));
         }

         void op_equal(void)
         {
            auto&& data1 = pop_back();
            auto&& data2 = pop_back();

            bool state = (data1 == data2);

            BinaryData bd;
            bd.append(state);
            stack_.push_back(std::move(bd));
         }

         void op_1add(void)
         {
            auto&& a = pop_back();
            auto aI = rawBinaryToInt(a);

            stack_.push_back(std::move(intToRawBinary(aI + 1)));
         }

         void op_1sub(void)
         {
            auto&& a = pop_back();
            auto aI = rawBinaryToInt(a);

            stack_.push_back(std::move(intToRawBinary(aI - 1)));
         }

         void op_negate(void)
         {
            auto&& a = pop_back();
            auto aI = rawBinaryToInt(a);

            stack_.push_back(std::move(intToRawBinary(-aI)));
         }

         void op_abs(void)
         {
            auto&& a = pop_back();
            auto aI = rawBinaryToInt(a);

            auto&& negA = intToRawBinary(abs(aI));
            stack_.push_back(negA);
         }

         void op_not(void)
         {
            auto&& a = pop_back();
            auto aI = rawBinaryToInt(a);

            if (aI != 0)
               aI = 0;
            else
               aI = 1;

            stack_.push_back(std::move(intToRawBinary(aI)));
         }

         void op_0notequal(void)
         {
            auto&& a = pop_back();
            auto aI = rawBinaryToInt(a);

            if (aI != 0)
               aI = 1;

            stack_.push_back(std::move(intToRawBinary(aI)));
         }

         void op_numequal(void)
         {
            auto&& b = pop_back();
            auto&& a = pop_back();

            auto aI = rawBinaryToInt(a);
            auto bI = rawBinaryToInt(b);

            bool state = (aI == bI);

            BinaryData bd;
            bd.append(state);
            stack_.push_back(std::move(bd));
         }

         void op_numnotequal()
         {
            auto&& b = pop_back();
            auto&& a = pop_back();

            auto aI = rawBinaryToInt(a);
            auto bI = rawBinaryToInt(b);

            bool state = (aI != bI);

            BinaryData bd;
            bd.append(state);
            stack_.push_back(std::move(bd));
         }

         void op_lessthan(void)
         {
            auto&& b = pop_back();
            auto&& a = pop_back();

            auto aI = rawBinaryToInt(a);
            auto bI = rawBinaryToInt(b);

            bool state = (aI < bI);

            BinaryData bd;
            bd.append(state);
            stack_.push_back(std::move(bd));
         }

         void op_lessthanorequal(void)
         {
            auto&& b = pop_back();
            auto&& a = pop_back();

            auto aI = rawBinaryToInt(a);
            auto bI = rawBinaryToInt(b);

            bool state = (aI <= bI);

            BinaryData bd;
            bd.append(state);
            stack_.push_back(std::move(bd));
         }

         void op_greaterthan(void)
         {
            auto&& b = pop_back();
            auto&& a = pop_back();

            auto aI = rawBinaryToInt(a);
            auto bI = rawBinaryToInt(b);

            bool state = (aI > bI);

            BinaryData bd;
            bd.append(state);
            stack_.push_back(std::move(bd));
         }

         void op_greaterthanorequal(void)
         {
            auto&& b = pop_back();
            auto&& a = pop_back();

            auto aI = rawBinaryToInt(a);
            auto bI = rawBinaryToInt(b);

            bool state = (aI >= bI);

            BinaryData bd;
            bd.append(state);
            stack_.push_back(std::move(bd));
         }

         void op_min(void);

         void op_max(void);

         void op_within(void)
         {
            auto&& top = pop_back();
            auto&& bot = pop_back();
            auto&& x = pop_back();

            auto xI = rawBinaryToInt(x);
            auto topI = rawBinaryToInt(top);
            auto botI = rawBinaryToInt(bot);

            bool state = (xI >= botI && xI < topI);

            BinaryData bd;
            bd.append(state);
            stack_.push_back(std::move(bd));
         }

         void op_booland(void)
         {
            auto&& b = pop_back();
            auto&& a = pop_back();

            auto aI = rawBinaryToInt(a);
            auto bI = rawBinaryToInt(b);

            uint8_t val = 0;
            if (aI != 0 && bI != 0)
               val = 1;

            BinaryData bd;
            bd.append(val);
            stack_.push_back(std::move(bd));
         }

         void op_boolor(void)
         {
            auto&& b = pop_back();
            auto&& a = pop_back();

            auto aI = rawBinaryToInt(a);
            auto bI = rawBinaryToInt(b);

            uint8_t val = 0;
            if (aI != 0 || bI != 0)
               val = 1;

            BinaryData bd;
            bd.append(val);
            stack_.push_back(std::move(bd));
         }

         void op_add(void)
         {
            auto&& b = pop_back();
            auto&& a = pop_back();

            auto aI = rawBinaryToInt(a);
            auto bI = rawBinaryToInt(b);

            auto cI = aI + bI;
            stack_.push_back(std::move(intToRawBinary(cI)));
         }

         void op_sub(void)
         {
            auto&& b = pop_back();
            auto&& a = pop_back();

            auto aI = rawBinaryToInt(a);
            auto bI = rawBinaryToInt(b);

            auto cI = aI - bI;
            stack_.push_back(std::move(intToRawBinary(cI)));
         }

         void op_checksig(void);

         void op_checkmultisig(void);

         void op_verify(void)
         {
            auto&& data = pop_back();
            isValid_ = notZero(data);

            if (!isValid_)
               throw ScriptException("op_verify returned false");
         }

         void process_p2wpkh(const BinaryData& scriptHash);
         void process_p2wsh(const BinaryData& scriptHash);

         //
      public:
         StackInterpreter(void) :
            txStubPtr_(nullptr), inputIndex_(-1)
         {
            //TODO: figure out rule detection
            flags_ = SCRIPT_VERIFY_P2SH;
         }

         StackInterpreter(const TransactionStub* stubPtr, unsigned inputId) :
            txStubPtr_(stubPtr), inputIndex_(inputId)
         {}

         void push_back(const BinaryData& data) { stack_.push_back(data); }
         BinaryData pop_back(void)
         {
            if (stack_.size() == 0)
               throw ScriptException("tried to pop an empty stack");

            auto data = stack_.back();
            stack_.pop_back();

            return data;
         }

         const BinaryData& stack_back(void) const
         {
            if (stack_.size() == 0)
               throw ScriptException("tried to peak an empty stack");

            return stack_.back();
         }

         void checkState(void);
         void processSW(BinaryDataRef outputScript);
         void setSegWitSigHashDataObject(std::shared_ptr<SigHashDataSegWit> shdo)
         {
            SHD_SW_ = shdo;
         }

         unsigned getFlags(void) const { return flags_; }
         void setFlags(unsigned flags) { flags_ = flags; }

         void processScript(const BinaryDataRef&, bool);
         void processScript(BinaryRefReader&, bool);

         const TxInEvalState& getTxInEvalState(void) const
         {
            return txInEvalState_;
         }
      };

      //////////////////////////////////////////////////////////////////////////
      struct ReversedStackEntry;

      ////
      enum StackValueEnum
      {
         StackValueType_Static,
         StackValueType_FromFeed,
         StackValueType_Sig,
         StackValueType_Multisig,
         StackValueType_Reference
      };

      ////
      struct StackValue
      {
      private:
         const StackValueEnum type_;

      public:
         StackValue(StackValueEnum type) :
            type_(type)
         {}

         virtual ~StackValue(void) = 0;

         StackValueEnum type(void) const { return type_; }
      };

      ////
      struct StackValue_Static : public StackValue
      {
         BinaryData value_;

         StackValue_Static(BinaryData val) :
            StackValue(StackValueType_Static), value_(std::move(val))
         {}
      };

      ////
      struct StackValue_Reference : public StackValue
      {
         std::shared_ptr<ReversedStackEntry> valueReference_;
         BinaryData value_;

         StackValue_Reference(std::shared_ptr<ReversedStackEntry> rsePtr) :
            StackValue(StackValueType_Reference), valueReference_(rsePtr)
         {}
      };

      ////
      struct StackValue_FromFeed : public StackValue
      {
         BinaryData requestString_;
         BinaryData value_;

         StackValue_FromFeed(const BinaryData& bd) :
            StackValue(StackValueType_FromFeed), requestString_(bd)
         {}
      };

      ////
      struct StackValue_Sig : public StackValue
      {
         std::shared_ptr<ReversedStackEntry> pubkeyRef_;
         BinaryData script_;

         StackValue_Sig(std::shared_ptr<ReversedStackEntry> ref) :
            StackValue(StackValueType_Sig), pubkeyRef_(ref)
         {}
      };

      ////
      struct StackValue_Multisig : public StackValue
      {
         BinaryData script_;

         StackValue_Multisig(const BinaryData& script) :
            StackValue(StackValueType_Multisig), script_(script)
         {}
      };

      ////////////////////////////////////////////////////////////////////////////////
      struct ReversedStackEntry
      {
      public:

         //static data is usually result of a pushdata opcode
         bool static_ = false; 
         BinaryData staticData_;

         //ptr to parent for op_dup style entries
         std::shared_ptr<ReversedStackEntry> parent_ = nullptr;

         //effective opcodes on this item
         std::vector<std::shared_ptr<OpCode>> opcodes_;

         //original value prior to opcode effect
         std::shared_ptr<StackValue> resolvedValue_;

      public:
         ReversedStackEntry(void)
         {}

         ReversedStackEntry(const BinaryData& data) :
            static_(true), staticData_(data)
         {}

         bool push_opcode(std::shared_ptr<OpCode> ocptr)
         {
            if (static_ && parent_ == nullptr)
               return false;

            if (parent_ != nullptr)
            {
               parent_->push_opcode(ocptr);
               return false;
            }

            opcodes_.push_back(ocptr);
            return true;
         }
      };

      //////////////////////////////////////////////////////////////////////////
      enum StackItemType
      {
         StackItemType_PushData,
         StackItemType_OpCode,
         StackItemType_Sig,
         StackItemType_MultiSig,
         StackItemType_SerializedScript
      };

      ////
      struct StackItem
      {
      protected:
         const unsigned id_;

      public:
         const StackItemType type_;

         StackItem(StackItemType type, unsigned id) :
            id_(id), type_(type)
         {}

         virtual ~StackItem(void) = 0;
         virtual bool isSame(const StackItem* obj) const = 0;
         unsigned getId(void) const { return id_; }

         virtual bool isValid(void) const { return true; }
         virtual void serialize(Codec_SignerState::StackEntryState&) const = 0;

         static std::shared_ptr<StackItem> deserialize(
            const Codec_SignerState::StackEntryState&);
      };

      ////
      struct StackItem_PushData : public StackItem
      {
         const BinaryData data_;

         StackItem_PushData(unsigned id, BinaryData&& data) :
            StackItem(StackItemType_PushData, id), data_(std::move(data))
         {}

         bool isSame(const StackItem* obj) const override;
         void serialize(Codec_SignerState::StackEntryState&) const override;
         bool isValid(void) const override { return !data_.empty(); }
      };

      ////
      struct StackItem_Sig : public StackItem
      {
         BinaryData pubkey_;
         BinaryData script_;
         SecureBinaryData sig_;

         StackItem_Sig(unsigned id, BinaryData& pubkey, BinaryData& script) :
            StackItem(StackItemType_Sig, id), 
            pubkey_(std::move(pubkey)), 
            script_(std::move(script))
         {}

         bool isSame(const StackItem* obj) const override; 
         void merge(const StackItem* obj);
         void serialize(Codec_SignerState::StackEntryState&) const override;
         void injectSig(SecureBinaryData& sig)
         {
            sig_ = std::move(sig);
         }
         bool isValid(void) const override 
         { 
            return !sig_.empty(); 
         }

      };

      ////
      struct StackItem_MultiSig : public StackItem
      {
         const BinaryData script_;

         std::map<unsigned, SecureBinaryData> sigs_;
         std::vector<BinaryData> pubkeyVec_;
         unsigned m_;

         StackItem_MultiSig(unsigned, BinaryData&);
         void setSig(unsigned id, SecureBinaryData& sig)
         {
            auto sigpair = std::make_pair(id, std::move(sig));
            sigs_.insert(std::move(sigpair));
         }

         bool isSame(const StackItem* obj) const override;
         void merge(const StackItem* obj);

         bool isValid(void) const override { return sigs_.size() == m_; }
         void serialize(Codec_SignerState::StackEntryState&) const override;
      };

      ////
      struct StackItem_OpCode : public StackItem
      {
         const uint8_t opcode_;

         StackItem_OpCode(unsigned id, uint8_t opcode) :
            StackItem(StackItemType_OpCode, id), 
            opcode_(opcode)
         {}

         bool isSame(const StackItem* obj) const override;
         void serialize(Codec_SignerState::StackEntryState&) const override;
      };

      ////
      struct StackItem_SerializedScript : public StackItem
      {
         const BinaryData data_;

         StackItem_SerializedScript(unsigned id, BinaryData&& data) :
            StackItem(StackItemType_SerializedScript, id), 
            data_(std::move(data))
         {}

         bool isSame(const StackItem* obj) const;
         void serialize(Codec_SignerState::StackEntryState&) const;
      };

      //////////////////////////////////////////////////////////////////////////
      class ResolvedStack
      {
         friend class StackResolver;

      private:
         bool isP2SH_ = false;

         std::vector<std::shared_ptr<StackItem>> stack_;
         std::shared_ptr<ResolvedStack> witnessStack_ = nullptr;

      public:
         bool isP2SH(void) const { return isP2SH_; }
         void flagP2SH(bool flag) { isP2SH_ = flag; }
         size_t stackSize(void) const { return stack_.size(); }

         std::shared_ptr<ResolvedStack> getWitnessStack(void) const 
         {
            return witnessStack_;
         }

         void setWitnessStack(std::shared_ptr<ResolvedStack> stack)
         {
            witnessStack_ = stack;
         }

         void setStackData(std::vector<std::shared_ptr<StackItem>> stack)
         {
            stack_.insert(stack_.end(), stack.begin(), stack.end());
         }

         const std::vector<std::shared_ptr<StackItem>>& getStack(void) const
         {
            return stack_;
         }
      };

      //////////////////////////////////////////////////////////////////////////
      class StackResolver : ScriptParser
      {
      private:
         std::deque<std::shared_ptr<ReversedStackEntry>> stack_;
         unsigned flags_ = 0;

         std::shared_ptr<ResolvedStack> resolvedStack_ = nullptr;
         unsigned opCodeCount_ = 0;
         bool opHash_ = false;
         bool isP2SH_ = false;
         bool isSW_ = false;

         const BinaryDataRef script_;
         std::shared_ptr<ResolverFeed> feed_;

      private:
         std::shared_ptr<ReversedStackEntry> pop_back(void)
         {
            std::shared_ptr<ReversedStackEntry> item;

            if (stack_.size() > 0)
            {
               item = stack_.back();
               stack_.pop_back();
            }
            else
               item = std::make_shared<ReversedStackEntry>();

            return item;
         }

         std::shared_ptr<ReversedStackEntry> getTopStackEntryPtr(void)
         {
            if (stack_.size() == 0)
               stack_.push_back(std::make_shared<ReversedStackEntry>());

            return stack_.back();
         }

         void processOpCode(const OpCode&);

         void push_int(unsigned i)
         {
            auto&& valBD = intToRawBinary(i);
            pushdata(valBD);
         }

         void pushdata(const BinaryData& data)
         {
            auto rse = std::make_shared<ReversedStackEntry>(data);
            stack_.push_back(rse);
         }

         void op_dup(void)
         {
            auto rsePtr = getTopStackEntryPtr();

            auto rseDup = std::make_shared<ReversedStackEntry>();
            rseDup->static_ = true;
            rseDup->parent_ = rsePtr;

            stack_.push_back(rseDup);
         }

         void push_op_code(const OpCode& oc)
         {
            auto rsePtr = std::make_shared<ReversedStackEntry>();
            auto ocPtr = std::make_shared<OpCode>(oc);

            rsePtr->push_opcode(ocPtr);
            stack_.push_back(rsePtr);
         }

         void op_1item(const OpCode& oc)
         {
            /***op_1item always preserves the item. 1 item operations only modify
            the existing item, they do not establish a relationship between several
            items, such operations should not reduce the stack depth.
            ***/

            auto ocPtr = std::make_shared<OpCode>(oc);
            auto item1 = getTopStackEntryPtr();
            item1->push_opcode(ocPtr);

            push_int(1);
         }

         void op_1item_verify(const OpCode& oc)
         {
            op_1item(oc);
            pop_back();
         }

         void op_2items(const OpCode& oc)
         {
            /***
            op_2items will always link 2 items. static items and references
            are culled.
            ***/

            auto item2 = pop_back();
            auto item1 = pop_back();

            if (item1->parent_ != item2)
            {
               auto eoc1 = std::make_shared<ExtendedOpCode>(oc);
               eoc1->itemIndex_ = 1;
               eoc1->referenceStackItemVec_.push_back(item2);
               if (item1->push_opcode(eoc1))
                  stack_.push_back(item1);
            }

            if (item2->parent_ != item1)
            {
               auto eoc2 = std::make_shared<ExtendedOpCode>(oc);
               eoc2->itemIndex_ = 2;
               eoc2->referenceStackItemVec_.push_back(item1);
               if (item2->push_opcode(eoc2))
                  stack_.push_back(item2);
            }

            push_int(1);
         }

         void op_2items_verify(const OpCode& oc)
         {
            op_2items(oc);
            pop_back();
         }

         void processScript(BinaryRefReader&);
         void resolveStack(void);

      public:
         StackResolver(BinaryDataRef script,
            std::shared_ptr<ResolverFeed> feed) :
            script_(script), feed_(feed)
         {}

         ~StackResolver(void)
         {
            for (auto& stackEntry : stack_)
            {
               stackEntry->parent_ = nullptr;
               stackEntry->opcodes_.clear();
            }
         }

         std::shared_ptr<ResolvedStack> getResolvedStack();
         unsigned getFlags(void) const { return flags_; }
         void setFlags(unsigned flags) { flags_ = flags; }
         std::shared_ptr<ResolverFeed> getFeed(
            void) const { return feed_; }
      };
   }; //namespace Signer
}; //namespace Armory

#endif
