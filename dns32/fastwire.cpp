// DNS wire encoding, transport, and wire shuffling only. No guest ALU or DNS answer cache.
//
// The sequencer below (`compile_plan`, `run`) interprets a wiring plan that was
// fetched from DNS: it concatenates string slices, sends the plan's lookups to
// the authoritative server, and copies the answers into wires. Every arithmetic
// result is a DNS answer; the plan for the next block is fetched from DNS in the
// same round trip as the state commit.
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <sys/socket.h>
#include <cerrno>
#include <cstring>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
using std::string;
static const size_t BATCH=64;   // pipelined requests per TCP write
static void u16(string& s,unsigned n){s.push_back(n>>8);s.push_back(n);}
static unsigned r16(const string& s,size_t p){if(p+2>s.size())throw std::runtime_error("Truncated DNS message");return ((unsigned char)s[p]<<8)|(unsigned char)s[p+1];}
static string pystr(PyObject* o){const char* s=PyUnicode_AsUTF8(o);if(!s)throw std::runtime_error("Expected DNS text");return s;}
static void name(string& s,const string& text){size_t p=0;while(p<text.size()){size_t e=text.find('.',p);if(e==string::npos)e=text.size();size_t n=e-p;if(n>63)throw std::runtime_error("DNS label too long");s.push_back(n);s.append(text,p,n);p=e+1;}s.push_back(0);}
static string txt(const string& s){string out;for(size_t p=0;p<s.size();p+=200){size_t n=std::min(size_t(200),s.size()-p);out.push_back(n);out.append(s,p,n);}if(s.empty())out.push_back(0);return out;}
static void rr(string& w,const string& owner,unsigned cls,const string& data){name(w,owner+".rv.test.");u16(w,16);u16(w,cls);w.append(4,'\0');u16(w,data.size());w+=data;}

// ---- encoders ---------------------------------------------------------------
static string encode_query(unsigned id,const string& owner){string w;u16(w,id);u16(w,0);u16(w,1);w.append(6,'\0');name(w,owner+".rv.test.");u16(w,16);u16(w,1);return w;}
static string encode_update(unsigned id,const std::vector<std::pair<string,string>>& changes,const string* previous,const string& prerequisite){
    if(changes.size()>1000)throw std::runtime_error("Invalid update size");
    string w;u16(w,id);u16(w,0x2800);u16(w,1);u16(w,previous?1:0);u16(w,changes.size()*2);u16(w,0);name(w,"rv.test.");u16(w,6);u16(w,1);
    if(previous)rr(w,prerequisite,1,txt(*previous));
    for(auto& c:changes){rr(w,c.first,255,"");rr(w,c.first,1,txt(c.second));}
    return w;}

// ---- transport --------------------------------------------------------------
static void sendall(int fd,const string& s){size_t p=0;while(p<s.size()){ssize_t n=send(fd,s.data()+p,s.size()-p,MSG_NOSIGNAL);if(n<0&&errno==EINTR)continue;if(n<=0)throw std::runtime_error(string("DNS send: ")+strerror(errno));p+=n;}}
static std::vector<string> do_exchange(int fd,const std::vector<string>& requests){
    if(requests.empty())throw std::runtime_error("Empty DNS batch");
    string packet;for(auto& r:requests){if(r.size()>65535)throw std::runtime_error("DNS packet too large");u16(packet,r.size());packet+=r;}
    sendall(fd,packet);
    std::vector<string> replies;string incoming;size_t cursor=0;
    auto need=[&](size_t count){while(incoming.size()-cursor<count){char buffer[65536];ssize_t got=recv(fd,buffer,sizeof(buffer),0);if(got<0&&errno==EINTR)continue;if(got<=0)throw std::runtime_error(got==0?"DNS connection closed":string("DNS recv: ")+strerror(errno));incoming.append(buffer,got);}};
    for(size_t i=0;i<requests.size();i++){need(2);unsigned size=r16(incoming,cursor);cursor+=2;need(size);replies.push_back(incoming.substr(cursor,size));cursor+=size;}
    if(cursor!=incoming.size())throw std::runtime_error("Unsolicited DNS response bytes");
    return replies;}

// ---- decoders ---------------------------------------------------------------
static size_t skip(const string& s,size_t p){while(true){if(p>=s.size())throw std::runtime_error("Truncated name");unsigned n=(unsigned char)s[p++];if(!n)return p;if((n&0xc0)==0xc0){if(p>=s.size())throw std::runtime_error("Truncated pointer");return p+1;}if(n&0xc0||p+n>s.size())throw std::runtime_error("Malformed name");p+=n;}}
static string parse_txt(const string& s){
    if(!(r16(s,2)&0x400)||r16(s,4)!=1)throw std::runtime_error("Expected authoritative answer");
    unsigned answers=r16(s,6),found=0;size_t p=skip(s,12)+4;string result;
    for(unsigned i=0;i<answers;i++){p=skip(s,p);unsigned type=r16(s,p),size=r16(s,p+8);p+=10;size_t end=p+size;if(end>s.size())throw std::runtime_error("Truncated RR");
        if(type==16){found++;while(p<end){unsigned n=(unsigned char)s[p++];if(p+n>end)throw std::runtime_error("Truncated TXT");result.append(s,p,n);p+=n;}}p=end;}
    if(found!=1)throw std::runtime_error("Expected exactly one TXT record");
    return result;}
static unsigned rcode(const string& reply,const string& what){unsigned flags=r16(reply,2);if(!(flags&0x8000))throw std::runtime_error("DNS reply is not a response for "+what);return flags&15;}

// ---- Python-facing primitives (unchanged behaviour) ------------------------
static PyObject* query(PyObject*,PyObject* args){unsigned id;const char* owner;if(!PyArg_ParseTuple(args,"Is",&id,&owner))return nullptr;try{string w=encode_query(id,owner);return PyBytes_FromStringAndSize(w.data(),w.size());}catch(const std::exception& e){PyErr_SetString(PyExc_ValueError,e.what());return nullptr;}}
static std::vector<std::pair<string,string>> changes_of(PyObject* changes){
    Py_ssize_t n=PySequence_Size(changes);if(n<0)throw std::runtime_error("Invalid update");
    std::vector<std::pair<string,string>> out;
    for(Py_ssize_t i=0;i<n;i++){PyObject* pair=PySequence_GetItem(changes,i);if(!pair)throw std::runtime_error("Invalid update");PyObject* a=PySequence_GetItem(pair,0);PyObject* b=PySequence_GetItem(pair,1);if(!a||!b){Py_XDECREF(a);Py_XDECREF(b);Py_DECREF(pair);throw std::runtime_error("Invalid update pair");}
        try{out.emplace_back(pystr(a),pystr(b));}catch(...){Py_DECREF(a);Py_DECREF(b);Py_DECREF(pair);throw;}Py_DECREF(a);Py_DECREF(b);Py_DECREF(pair);}
    return out;}
static PyObject* update(PyObject*,PyObject* args){unsigned id;PyObject *changes,*previous;const char* prerequisite;if(!PyArg_ParseTuple(args,"IOOs",&id,&changes,&previous,&prerequisite))return nullptr;
    try{string prev;const string* pp=nullptr;if(previous!=Py_None){prev=pystr(previous);pp=&prev;}string w=encode_update(id,changes_of(changes),pp,prerequisite);return PyBytes_FromStringAndSize(w.data(),w.size());}catch(const std::exception& e){PyErr_SetString(PyExc_ValueError,e.what());return nullptr;}}
static PyObject* exchange(PyObject*,PyObject* args){int fd;PyObject* requests;if(!PyArg_ParseTuple(args,"iO",&fd,&requests))return nullptr;try{Py_ssize_t n=PySequence_Size(requests);if(n<1||(size_t)n>BATCH)throw std::runtime_error("Batch must contain 1..64 requests");std::vector<string> reqs;
    for(Py_ssize_t i=0;i<n;i++){PyObject* item=PySequence_GetItem(requests,i);char* data;Py_ssize_t len;if(!item||PyBytes_AsStringAndSize(item,&data,&len)<0){Py_XDECREF(item);throw std::runtime_error("Expected DNS bytes");}reqs.emplace_back(data,len);Py_DECREF(item);}
    std::vector<string> replies=do_exchange(fd,reqs);PyObject* out=PyList_New(n);for(Py_ssize_t i=0;i<n;i++)PyList_SET_ITEM(out,i,PyBytes_FromStringAndSize(replies[i].data(),replies[i].size()));return out;}catch(const std::exception& e){PyErr_SetString(PyExc_RuntimeError,e.what());return nullptr;}}
static PyObject* parse(PyObject*,PyObject* arg){char* bytes;Py_ssize_t len;if(PyBytes_AsStringAndSize(arg,&bytes,&len)<0)return nullptr;try{string r=parse_txt(string(bytes,len));return PyUnicode_DecodeASCII(r.data(),r.size(),"strict");}catch(const std::exception& e){PyErr_SetString(PyExc_ValueError,e.what());return nullptr;}}

// ---- compiled plans ---------------------------------------------------------
struct Part{bool literal;string text;int wire;int start;int end;};
struct Step{int out;std::vector<Part> parts;};
struct Stage{std::vector<Step> copies,queries;};
struct Plan{std::vector<Stage> stages;std::vector<std::pair<std::vector<Part>,std::vector<Part>>> writes;int wires;int state_write;std::vector<std::pair<int,int>> skips;};
static const char* CAPSULE="dns32.plan";
static void plan_free(PyObject* cap){delete static_cast<Plan*>(PyCapsule_GetPointer(cap,CAPSULE));}
static std::vector<Part> parts_of(PyObject* seq){
    Py_ssize_t n=PySequence_Size(seq);if(n<0)throw std::runtime_error("Expected parts");std::vector<Part> out;
    for(Py_ssize_t i=0;i<n;i++){PyObject* p=PySequence_GetItem(seq,i);if(!p)throw std::runtime_error("Expected part");
        try{if(PyUnicode_Check(p))out.push_back({true,pystr(p),0,0,0});
            else{int wire,start,end;if(!PyArg_ParseTuple(p,"iii",&wire,&start,&end))throw std::runtime_error("Expected (wire,start,end)");out.push_back({false,"",wire,start,end});}}
        catch(...){Py_DECREF(p);throw;}Py_DECREF(p);}
    return out;}
static std::vector<Step> steps_of(PyObject* seq){
    Py_ssize_t n=PySequence_Size(seq);if(n<0)throw std::runtime_error("Expected steps");std::vector<Step> out;
    for(Py_ssize_t i=0;i<n;i++){PyObject* s=PySequence_GetItem(seq,i);if(!s)throw std::runtime_error("Expected step");int wire;PyObject* parts;
        if(!PyArg_ParseTuple(s,"iO",&wire,&parts)){Py_DECREF(s);throw std::runtime_error("Expected (wire,parts)");}
        try{out.push_back({wire,parts_of(parts)});}catch(...){Py_DECREF(s);throw;}Py_DECREF(s);}
    return out;}
static PyObject* compile_plan(PyObject*,PyObject* args){PyObject *stages,*writes,*skips;int wires,state_write;if(!PyArg_ParseTuple(args,"OOOii",&stages,&writes,&skips,&wires,&state_write))return nullptr;
    Plan* plan=new Plan;plan->wires=wires;plan->state_write=state_write;
    try{Py_ssize_t n=PySequence_Size(stages);for(Py_ssize_t i=0;i<n;i++){PyObject* st=PySequence_GetItem(stages,i);PyObject *copies,*queries;if(!st||!PyArg_ParseTuple(st,"OO",&copies,&queries)){Py_XDECREF(st);throw std::runtime_error("Expected (copies,queries)");}
            try{plan->stages.push_back({steps_of(copies),steps_of(queries)});}catch(...){Py_DECREF(st);throw;}Py_DECREF(st);}
        n=PySequence_Size(writes);for(Py_ssize_t i=0;i<n;i++){PyObject* w=PySequence_GetItem(writes,i);PyObject *nm,*val;if(!w||!PyArg_ParseTuple(w,"OO",&nm,&val)){Py_XDECREF(w);throw std::runtime_error("Expected (name,value)");}
            try{plan->writes.emplace_back(parts_of(nm),parts_of(val));}catch(...){Py_DECREF(w);throw;}Py_DECREF(w);}
        n=PySequence_Size(skips);for(Py_ssize_t i=0;i<n;i++){PyObject* s=PySequence_GetItem(skips,i);int cond,length;if(!s||!PyArg_ParseTuple(s,"ii",&cond,&length)){Py_XDECREF(s);throw std::runtime_error("Expected (cond,length)");}plan->skips.emplace_back(cond,length);Py_DECREF(s);}
        if(state_write<0||(size_t)state_write>=plan->writes.size())throw std::runtime_error("Invalid state write index");
        for(auto& st:plan->stages)for(auto* v:{&st.copies,&st.queries})for(auto& s:*v){if(s.out<0||s.out>=wires)throw std::runtime_error("Wire index out of range");for(auto& p:s.parts)if(!p.literal&&(p.wire<0||p.wire>=wires))throw std::runtime_error("Wire index out of range");}
    }catch(const std::exception& e){delete plan;PyErr_SetString(PyExc_ValueError,e.what());return nullptr;}
    return PyCapsule_New(plan,CAPSULE,plan_free);}

static string build(const std::vector<Part>& parts,const std::vector<string>& wires){string s;for(auto& p:parts){if(p.literal)s+=p.text;else{const string& w=wires[p.wire];if(p.start<0)s+=w;else{if((size_t)p.end>w.size()||p.start>p.end)throw std::runtime_error("Wire slice out of range");s.append(w,p.start,p.end-p.start);}}}return s;}

struct Counters{long transactions=0,payload=0,trips=0;};
static std::vector<string> exchange_counted(int fd,const std::vector<string>& reqs,Counters& c){std::vector<string> replies=do_exchange(fd,reqs);c.trips++;c.transactions+=reqs.size();for(size_t i=0;i<reqs.size();i++)c.payload+=reqs[i].size()+replies[i].size();return replies;}

// run(fd, plan, state, next_id, sample_room) ->
//   (new_state, next_plan_text_or_None, [arm skipped...], transactions, payload_bytes, round_trips, next_id, [(name,req,resp)...])
static PyObject* run(PyObject*,PyObject* args){int fd;PyObject* cap;const char* state_c;unsigned next_id;int sample_room;
    if(!PyArg_ParseTuple(args,"iOsIi",&fd,&cap,&state_c,&next_id,&sample_room))return nullptr;
    Plan* plan=static_cast<Plan*>(PyCapsule_GetPointer(cap,CAPSULE));if(!plan)return nullptr;
    try{
        std::vector<string> wires(plan->wires);wires[0]=state_c;Counters c;std::vector<std::pair<string,std::pair<size_t,size_t>>> samples;
        auto id=[&](){next_id=(next_id+1)&65535;return next_id;};
        for(auto& stage:plan->stages){
            for(auto& s:stage.copies)wires[s.out]=build(s.parts,wires);
            for(size_t start=0;start<stage.queries.size();start+=BATCH){
                size_t end=std::min(stage.queries.size(),start+BATCH);std::vector<string> reqs;std::vector<string> names;std::map<string,size_t> ids;
                for(size_t i=start;i<end;i++){names.push_back(build(stage.queries[i].parts,wires));reqs.push_back(encode_query(id(),names.back()));ids[reqs.back().substr(0,2)]=i-start;}
                if(ids.size()!=reqs.size())throw std::runtime_error("Duplicate DNS request IDs");
                std::vector<string> replies=exchange_counted(fd,reqs,c);
                std::vector<char> seen(reqs.size(),0);
                for(auto& reply:replies){auto it=ids.find(reply.substr(0,2));if(it==ids.end()||seen[it->second]++)throw std::runtime_error("Unexpected DNS response ID");size_t i=it->second;
                    unsigned rc=rcode(reply,names[i]);if(rc)throw std::runtime_error("DNS RCODE "+std::to_string(rc)+" for "+names[i]);
                    wires[stage.queries[start+i].out]=parse_txt(reply);
                    if(sample_room>0){samples.emplace_back(names[i],std::make_pair(reqs[i].size(),reply.size()));sample_room--;}}
            }
        }
        std::vector<std::pair<string,string>> changes;for(auto& w:plan->writes)changes.emplace_back(build(w.first,wires),build(w.second,wires));
        const string& new_state=changes[plan->state_write].second;if(new_state.size()<8)throw std::runtime_error("Invalid state value");
        string previous=wires[0];string next_name=new_state.substr(0,8)+".code";
        std::vector<string> reqs{encode_update(id(),changes,&previous,"state.reg"),encode_query(id(),next_name)};
        std::vector<string> replies=exchange_counted(fd,reqs,c);
        std::map<string,size_t> ids{{reqs[0].substr(0,2),0},{reqs[1].substr(0,2),1}};string ordered[2];std::vector<char> seen(2,0);
        for(auto& reply:replies){auto it=ids.find(reply.substr(0,2));if(it==ids.end()||seen[it->second]++)throw std::runtime_error("Unexpected DNS response ID");ordered[it->second]=reply;}
        unsigned rc=rcode(ordered[0],"UPDATE");if(rc)throw std::runtime_error("DNS RCODE "+std::to_string(rc)+" for UPDATE");
        if(sample_room>0){samples.emplace_back("UPDATE",std::make_pair(reqs[0].size(),ordered[0].size()));sample_room--;}
        rc=rcode(ordered[1],next_name);PyObject* next_raw;
        if(rc==3){Py_INCREF(Py_None);next_raw=Py_None;}   // speculative fetch past the last plan
        else if(rc)throw std::runtime_error("DNS RCODE "+std::to_string(rc)+" for "+next_name);
        else{string t=parse_txt(ordered[1]);next_raw=PyUnicode_DecodeASCII(t.data(),t.size(),"strict");if(!next_raw)return nullptr;
            if(sample_room>0){samples.emplace_back(next_name,std::make_pair(reqs[1].size(),ordered[1].size()));sample_room--;}}
        PyObject* skipped=PyList_New(plan->skips.size());for(size_t i=0;i<plan->skips.size();i++){PyObject* b=wires[plan->skips[i].first]=="1"?Py_True:Py_False;Py_INCREF(b);PyList_SET_ITEM(skipped,i,b);}
        PyObject* samp=PyList_New(samples.size());for(size_t i=0;i<samples.size();i++)PyList_SET_ITEM(samp,i,Py_BuildValue("(snn)",samples[i].first.c_str(),(Py_ssize_t)samples[i].second.first,(Py_ssize_t)samples[i].second.second));
        PyObject* result=Py_BuildValue("(s#NNlllIN)",new_state.data(),(Py_ssize_t)new_state.size(),next_raw,skipped,c.transactions,c.payload,c.trips,next_id,samp);
        return result;
    }catch(const std::exception& e){PyErr_SetString(PyExc_RuntimeError,e.what());return nullptr;}}

static PyMethodDef methods[]={{"query",query,METH_VARARGS,"Encode QUERY"},{"update",update,METH_VARARGS,"Encode UPDATE"},{"exchange",exchange,METH_VARARGS,"Exchange a bounded DNS batch"},{"parse_txt",parse,METH_O,"Decode TXT"},
    {"compile_plan",compile_plan,METH_VARARGS,"Decode a wiring plan into a native structure"},{"run",run,METH_VARARGS,"Run one block: lookups, commit, next plan fetch"},{nullptr,nullptr,0,nullptr}};
static PyModuleDef mod={PyModuleDef_HEAD_INIT,"_fastwire",nullptr,-1,methods};
PyMODINIT_FUNC PyInit__fastwire(){return PyModule_Create(&mod);}
